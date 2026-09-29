"""Clear all Ticket Audit Agent data while keeping the database tables."""

from db_config import DBConfig
from db_modal import AuditHistory, Incident


CONFIRMATION = "DELETE ALL INCIDENT DATA"


def main() -> int:
    db_config = DBConfig()
    session = db_config.get_session()

    try:
        incident_count = session.query(Incident).count()
        history_count = session.query(AuditHistory).count()

        print("This will permanently delete Ticket Audit Agent data:")
        print(f"  Incidents: {incident_count}")
        print(f"  Audit history rows: {history_count}")
        print("Database tables and schema will be kept.")
        answer = input(f'Type "{CONFIRMATION}" to continue: ').strip()

        if answer != CONFIRMATION:
            print("Cancelled. No data was deleted.")
            return 1

        # Delete dependent rows first for databases without cascading foreign keys.
        session.query(AuditHistory).delete(synchronize_session=False)
        session.query(Incident).delete(synchronize_session=False)
        session.commit()

        print(f"Deleted {incident_count} incident(s) and {history_count} audit history row(s).")
        return 0
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
        db_config.close()


if __name__ == "__main__":
    raise SystemExit(main())
