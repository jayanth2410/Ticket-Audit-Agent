"""
redis_job_store.py — Shared job state backed by Redis
======================================================

WHY THIS EXISTS
---------------
The old architecture stored all job state in a plain Python dict (JOBS = {})
inside the Flask process. This broke under Nginx because:

  - Nginx can have multiple Gunicorn workers, each with their own copy of JOBS.
  - A cancel request routed to Worker-2 would find JOBS empty because the job
    was started by Worker-1.
  - Result: cancellation silently failed and the audit ran to completion (or
    got stuck) with no way to stop it.

HOW THIS FIXES IT
-----------------
Redis is a single, shared store that every worker can read and write.

  - Job metadata (status, params, paths, error) → Redis Hash  (HSET / HGETALL)
  - Log lines for SSE streaming               → Redis List   (RPUSH / BLPOP)
  - Cancel signal                             → Redis Key    (SET / EXISTS / DEL)

Any worker can:
  - Read job status           →  get_job(job_id)
  - Write a log line          →  push_log(job_id, message)
  - Read the next log line    →  pop_log(job_id, timeout)
  - Request cancellation      →  set_cancel(job_id)
  - Check if cancelled        →  is_cancelled(job_id)

The audit subprocess (which runs in a separate spawned process) uses
push_log() and is_cancelled() throughout the pipeline.  The Flask routes
use get_job(), set_job_field(), and set_cancel().

REDIS KEY LAYOUT
----------------
  job:{job_id}          → Hash  — all job metadata fields
  log:{job_id}          → List  — SSE log lines (FIFO queue)
  cancel:{job_id}       → Key   — exists means "cancel requested"

TTL
---
All keys are set with a 2-hour TTL so Redis never fills up from abandoned jobs.
"""

import json
import time
import logging
from typing import Optional

import redis

logger = logging.getLogger(__name__)

JOB_TTL = 7200  # 2 hours


class RedisUnavailableError(Exception):
    """Raised when a Redis operation fails because Redis is unreachable."""
    pass


class RedisJobStore:
    """
    Thin wrapper around a Redis connection that provides a job-store interface.

    Usage (in Flask routes):
        store = RedisJobStore()
        store.create_job(job_id, params={...})
        store.set_job_field(job_id, "status", "running")
        store.push_log(job_id, "Fetching incidents...")
        store.set_cancel(job_id)

    Usage (inside audit subprocess):
        store = RedisJobStore()
        store.push_log(job_id, "Auditing INC001...")
        if store.is_cancelled(job_id):
            raise JobCancelled(...)
    """

    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0,
                 password: Optional[str] = None):
        """
        Create a Redis connection.  Each process (Flask worker or audit subprocess)
        creates its own RedisJobStore instance so connections are not shared across
        fork boundaries.
        """
        self._host     = host
        self._port     = port
        self._db       = db
        self._password = password
        self._r        = self._connect()

    def _connect(self) -> redis.Redis:
        return redis.Redis(
            host             = self._host,
            port             = self._port,
            db               = self._db,
            password         = self._password,
            decode_responses = True,
            socket_timeout          = 2,
            socket_connect_timeout  = 2,
        )

    def _exec(self, fn):
        """
        Execute a Redis operation and convert any connection/timeout error
        into RedisUnavailableError so callers get a consistent exception type.
        """
        try:
            return fn()
        except (redis.ConnectionError, redis.TimeoutError) as e:
            raise RedisUnavailableError(f"Redis unavailable: {e}") from e
        except redis.RedisError as e:
            raise RedisUnavailableError(f"Redis error: {e}") from e

    # ── Key helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _job_key(job_id: str) -> str:
        return f"job:{job_id}"

    @staticmethod
    def _log_key(job_id: str) -> str:
        return f"log:{job_id}"

    @staticmethod
    def _cancel_key(job_id: str) -> str:
        return f"cancel:{job_id}"

    # ── Job metadata (Redis Hash) ─────────────────────────────────────────────

    def create_job(self, job_id: str, params: dict,
                   excel_path: str = "", record_path: str = "") -> None:
        key = self._job_key(job_id)
        fields = {
            "job_id"           : job_id,
            "status"           : "running",
            "cancel_requested" : "0",
            "error"            : "",
            "excel_path"       : excel_path,
            "record_path"      : record_path,
            "created_at"       : str(time.time()),
            "finished_at"      : "",
            "params"           : json.dumps(params),
            "results"          : "",
        }
        for field, value in fields.items():
            self._exec(lambda f=field, v=value: self._r.hset(key, f, v))
        self._exec(lambda: self._r.expire(key, JOB_TTL))

    def get_job(self, job_id: str) -> Optional[dict]:
        raw = self._exec(lambda: self._r.hgetall(self._job_key(job_id)))
        if not raw:
            return None

        job = dict(raw)

        # Deserialise nested JSON fields
        if job.get("params"):
            try:
                job["params"] = json.loads(job["params"])
            except (json.JSONDecodeError, TypeError):
                pass

        if job.get("results"):
            try:
                job["results"] = json.loads(job["results"])
            except (json.JSONDecodeError, TypeError):
                pass

        # Normalise boolean-ish fields
        job["cancel_requested"] = job.get("cancel_requested") == "1"

        # Numeric timestamps
        for field in ("created_at", "finished_at"):
            val = job.get(field)
            if val:
                try:
                    job[field] = float(val)
                except (TypeError, ValueError):
                    job[field] = 0.0

        return job

    def job_exists(self, job_id: str) -> bool:
        return self._exec(lambda: self._r.exists(self._job_key(job_id))) == 1

    def set_job_field(self, job_id: str, field: str, value) -> None:
        if isinstance(value, (dict, list)):
            value = json.dumps(value, default=str)
        elif isinstance(value, bool):
            value = "1" if value else "0"
        elif value is None:
            value = ""

        self._exec(lambda: self._r.hset(self._job_key(job_id), field, str(value)))
        self._exec(lambda: self._r.expire(self._job_key(job_id), JOB_TTL))

    def set_job_fields(self, job_id: str, fields: dict) -> None:
        """Update multiple fields at once."""
        for k, v in fields.items():
            self.set_job_field(job_id, k, v)

    def finish_job(self, job_id: str, status: str,
                   results: Optional[dict] = None,
                   error: str = "") -> None:
        """Mark a job as finished (done / error / cancelled)."""
        self.set_job_fields(job_id, {
            "status"     : status,
            "error"      : error,
            "finished_at": str(time.time()),
            "results"    : json.dumps(results, default=str) if results else "",
        })

    def list_active_job_ids(self) -> list[str]:
        active = []
        for key in self._exec(lambda: list(self._r.scan_iter("job:*"))):
            jid    = key.removeprefix("job:")
            status = self._exec(lambda: self._r.hget(key, "status")) or ""
            if status in ("running", "cancelling"):
                active.append(jid)
        return active

    # ── Log queue (Redis List) ────────────────────────────────────────────────

    def push_log(self, job_id: str, message: str) -> None:
        key = self._log_key(job_id)
        self._exec(lambda: self._r.rpush(key, message))
        self._exec(lambda: self._r.expire(key, JOB_TTL))

    def pop_log(self, job_id: str, timeout: int = 20) -> Optional[str]:
        """
        Wait up to `timeout` seconds for the next log line.

        Redis 3.x does not support BLPOP with a float timeout reliably —
        it returns None immediately in some client versions.  We use a
        polling loop with LPOP instead, which works on all Redis versions.
        """
        key      = self._log_key(job_id)
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self._exec(lambda: self._r.lpop(key))
            if result is not None:
                return result
            time.sleep(0.2)   # 200 ms poll interval
        return None           # timed out — caller sends SSE heartbeat

    # ── Cancel signal (Redis Key) ─────────────────────────────────────────────

    def set_cancel(self, job_id: str) -> None:
        self._exec(lambda: self._r.set(self._cancel_key(job_id), "1", ex=JOB_TTL))
        current = self._exec(lambda: self._r.hget(self._job_key(job_id), "status")) or ""
        if current == "running":
            self._exec(lambda: self._r.hset(self._job_key(job_id), "status", "cancelling"))
            self._exec(lambda: self._r.hset(self._job_key(job_id), "cancel_requested", "1"))
        self._exec(lambda: self._r.expire(self._job_key(job_id), JOB_TTL))

    def is_cancelled(self, job_id: str) -> bool:
        return self._exec(lambda: self._r.exists(self._cancel_key(job_id))) == 1

    def clear_cancel(self, job_id: str) -> None:
        self._exec(lambda: self._r.delete(self._cancel_key(job_id)))

    def delete_job(self, job_id: str) -> None:
        self._exec(lambda: self._r.delete(
            self._job_key(job_id),
            self._log_key(job_id),
            self._cancel_key(job_id),
        ))

    def ping(self) -> bool:
        """Return True if Redis is reachable."""
        try:
            return self._r.ping()
        except redis.RedisError:
            return False
        except Exception:
            return False
