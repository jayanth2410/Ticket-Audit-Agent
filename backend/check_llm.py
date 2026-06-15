# test_contact_metrics.py

from llm import LLM

llm = LLM()

test_cases = [

    # ── Case 1: Clear contact + user confirmed ────────────────────────────────
    {
        "id"         : 1,
        "description": "Associate called user, user confirmed fix working",
        "work_notes" : [
            {"sys_created_on": "2026-05-20 10:00:00", "value": "Called user to discuss the issue. User confirmed the problem."},
            {"sys_created_on": "2026-05-21 14:00:00", "value": "Applied the fix. Restarted the service."},
            {"sys_created_on": "2026-05-21 15:00:00", "value": "Called user again. User confirmed issue is resolved and working fine."},
        ],
        "close_notes" : "Issue resolved. User confirmed.",
        "reopen_count": 0,
        "reopened_time": "",
        "expected"   : {"user_contact": "Yes", "user_confirmation": "Yes", "reopened_user_connect": "NA"},
    },

    # ── Case 2: No contact at all ─────────────────────────────────────────────
    {
        "id"         : 2,
        "description": "No user contact documented anywhere",
        "work_notes" : [
            {"sys_created_on": "2026-05-20 10:00:00", "value": "Investigated the issue. Found corrupted driver."},
            {"sys_created_on": "2026-05-20 11:00:00", "value": "Reinstalled the driver. Issue resolved."},
        ],
        "close_notes" : "Driver reinstalled.",
        "reopen_count": 0,
        "reopened_time": "",
        "expected"   : {"user_contact": "No", "user_confirmation": "No", "reopened_user_connect": "NA"},
    },

    # ── Case 3: 3-strike process followed ─────────────────────────────────────
    {
        "id"         : 3,
        "description": "User unavailable, 3 attempts made before closing",
        "work_notes" : [
            {"sys_created_on": "2026-05-20 09:00:00", "value": "1st attempt - Called user. No answer. Left voicemail."},
            {"sys_created_on": "2026-05-21 09:00:00", "value": "2nd attempt - Sent email to user. No response."},
            {"sys_created_on": "2026-05-22 09:00:00", "value": "3rd attempt - Called again. No response. Closing ticket as per 3 strike policy."},
        ],
        "close_notes" : "Closed after 3 unsuccessful contact attempts.",
        "reopen_count": 0,
        "reopened_time": "",
        "expected"   : {"user_contact": "Yes", "user_confirmation": "Yes", "reopened_user_connect": "NA"},
    },

    # ── Case 4: Contact made but only 1 attempt, user unavailable ────────────
    {
        "id"         : 4,
        "description": "Contact made once, user unavailable, closed without 3-strike",
        "work_notes" : [
            {"sys_created_on": "2026-05-20 09:00:00", "value": "Called user. No answer."},
            {"sys_created_on": "2026-05-20 10:00:00", "value": "Resolved the issue on the backend."},
        ],
        "close_notes" : "Fixed on backend.",
        "reopen_count": 0,
        "reopened_time": "",
        "expected"   : {"user_contact": "Yes", "user_confirmation": "No", "reopened_user_connect": "NA"},
    },

    # ── Case 5: Ticket reopened, associate reconnected ────────────────────────
    {
        "id"         : 5,
        "description": "Ticket reopened, associate called user after reopen",
        "work_notes" : [
            {"sys_created_on": "2026-05-20 09:00:00", "value": "Resolved the issue. User confirmed working."},
            {"sys_created_on": "2026-05-22 10:00:00", "value": "Ticket reopened by user. Called user to understand the problem again."},
            {"sys_created_on": "2026-05-22 11:00:00", "value": "Applied additional fix. User confirmed resolved."},
        ],
        "close_notes" : "Issue fully resolved after reopen.",
        "reopen_count": 1,
        "reopened_time": "2026-05-22 09:00:00",
        "expected"   : {"user_contact": "Yes", "user_confirmation": "Yes", "reopened_user_connect": "Yes"},
    },

    # ── Case 6: Ticket reopened, no contact after reopen ─────────────────────
    {
        "id"         : 6,
        "description": "Ticket reopened but no user contact documented after reopen",
        "work_notes" : [
            {"sys_created_on": "2026-05-20 09:00:00", "value": "Fixed the issue."},
            {"sys_created_on": "2026-05-22 10:00:00", "value": "Investigated again after reopen. Applied patch silently."},
        ],
        "close_notes" : "Patch applied.",
        "reopen_count": 1,
        "reopened_time": "2026-05-22 09:00:00",
        "expected"   : {"user_contact": "No", "user_confirmation": "No", "reopened_user_connect": "No"},
    },

    # ── Case 7: Contact via email only ────────────────────────────────────────
    {
        "id"         : 7,
        "description": "Associate contacted user only via email, user replied",
        "work_notes" : [
            {"sys_created_on": "2026-05-20 10:00:00", "value": "Sent email to user requesting additional information about the issue."},
            {"sys_created_on": "2026-05-20 14:00:00", "value": "User replied via email with the required details. Proceeding with fix."},
            {"sys_created_on": "2026-05-20 16:00:00", "value": "Fix applied. Emailed user to confirm. User confirmed working."},
        ],
        "close_notes" : "Resolved via email communication.",
        "reopen_count": 0,
        "reopened_time": "",
        "expected"   : {"user_contact": "Yes", "user_confirmation": "Yes", "reopened_user_connect": "NA"},
    },

    # ── Case 8: Auto-generated ticket, no user involved ───────────────────────
    {
        "id"         : 8,
        "description": "Auto-generated monitoring alert, no user contact needed",
        "work_notes" : [
            {"sys_created_on": "2026-05-20 02:00:00", "value": "Auto-generated alert: CPU usage exceeded threshold on server SRV-001."},
            {"sys_created_on": "2026-05-20 02:30:00", "value": "Investigated server. Cleared temp files and restarted services. CPU back to normal."},
        ],
        "close_notes" : "Server issue resolved automatically.",
        "reopen_count": 0,
        "reopened_time": "",
        "expected"   : {"user_contact": "NA", "user_confirmation": "NA", "reopened_user_connect": "NA"},
    },

    # ── Case 9: Contact via Teams, informal confirmation ──────────────────────
    {
        "id"         : 9,
        "description": "Contact via Teams message, user gave informal confirmation",
        "work_notes" : [
            {"sys_created_on": "2026-05-20 11:00:00", "value": "Pinged user on Teams to check if the issue is still occurring."},
            {"sys_created_on": "2026-05-20 11:30:00", "value": "User replied on Teams saying all good now, no more issues."},
        ],
        "close_notes" : "User confirmed via Teams.",
        "reopen_count": 0,
        "reopened_time": "",
        "expected"   : {"user_contact": "Yes", "user_confirmation": "Yes", "reopened_user_connect": "NA"},
    },

    # ── Case 10: 2 attempts only, ticket closed without 3rd ──────────────────
    {
        "id"         : 10,
        "description": "2 contact attempts made, 3-strike not completed before closing",
        "work_notes" : [
            {"sys_created_on": "2026-05-20 09:00:00", "value": "Called user. No response."},
            {"sys_created_on": "2026-05-21 09:00:00", "value": "Sent email to user. No reply received."},
            {"sys_created_on": "2026-05-21 15:00:00", "value": "Closing the ticket."},
        ],
        "close_notes" : "Closed after 2 attempts.",
        "reopen_count": 0,
        "reopened_time": "",
        "expected"   : {"user_contact": "Yes", "user_confirmation": "No", "reopened_user_connect": "NA"},
    },
]


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":

    passed = 0
    failed = 0

    print("=" * 70)
    print("CONTACT METRICS LLM TEST")
    print("=" * 70)

    for tc in test_cases:
        result = llm.contact_metrics_analyser(
            work_notes    = tc["work_notes"],
            close_notes   = tc["close_notes"],
            reopen_count  = tc["reopen_count"],
            reopened_time = tc["reopened_time"],
        )

        expected = tc["expected"]
        match    = result == expected

        status = "✓ PASS" if match else "✗ FAIL"
        if match:
            passed += 1
        else:
            failed += 1

        print(f"\n[{tc['id']}] {tc['description']}")
        print(f"  Status   : {status}")

        if not match:
            for key in expected:
                exp = expected[key]
                got = result.get(key, "?")
                indicator = "✓" if exp == got else "✗"
                print(f"  {indicator} {key:<30} expected={exp}  got={got}")
        else:
            print(f"  Result   : {result}")

    print("\n" + "=" * 70)
    print(f"RESULTS: {passed} passed / {failed} failed out of {len(test_cases)}")
    print("=" * 70)