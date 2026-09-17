"""Key tests for the /calendar-diff endpoint.

Run from the repository root:  python -m pytest tests/ -v
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

CAL_HEAD = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//diff-api//EN\r\n"
CAL_TAIL = "END:VCALENDAR\r\n"


def cal(*events: str) -> str:
    return CAL_HEAD + "".join(events) + CAL_TAIL


def event(*lines: str) -> str:
    return "BEGIN:VEVENT\r\n" + "".join(line + "\r\n" for line in lines) + "END:VEVENT\r\n"


def event_utc(uid: str, start: str, end: str, *extra: str) -> str:
    return event(f"UID:{uid}", f"DTSTART:{start}", f"DTEND:{end}", *extra)


def diff(old_ics: str, new_ics: str,
         window_start="2026-01-01T00:00:00Z", window_end="2026-02-01T00:00:00Z"):
    return client.post(
        "/calendar-diff",
        json={
            "old_ics": old_ics,
            "new_ics": new_ics,
            "window_start": window_start,
            "window_end": window_end,
        },
    )


def identities(instances) -> list[tuple[str, str | None]]:
    return [(i["uid"], i["recurrence_id"]) for i in instances]


def modified_identities(modified) -> list[tuple[str, str | None]]:
    return identities([m["after"] for m in modified])


# ---------------------------------------------------------------------------
# added / removed
# ---------------------------------------------------------------------------

def test_instance_only_in_new_is_added():
    old = cal(event_utc("evt-1", "20260105T090000Z", "20260105T100000Z"))
    new = cal(
        event_utc("evt-1", "20260105T090000Z", "20260105T100000Z"),
        event_utc("evt-2", "20260106T090000Z", "20260106T100000Z", "SUMMARY:new"),
    )
    resp = diff(old, new)
    assert resp.status_code == 200
    body = resp.json()
    assert identities(body["added"]) == [("evt-2", None)]
    assert body["added"][0]["summary"] == "new"
    assert body["removed"] == []
    assert body["modified"] == []


def test_instance_only_in_old_is_removed():
    old = cal(
        event_utc("evt-1", "20260105T090000Z", "20260105T100000Z"),
        event_utc("evt-2", "20260106T090000Z", "20260106T100000Z"),
    )
    new = cal(event_utc("evt-1", "20260105T090000Z", "20260105T100000Z"))
    resp = diff(old, new)
    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] == []
    assert identities(body["removed"]) == [("evt-2", None)]
    assert body["modified"] == []


def test_identical_calendars_produce_empty_diff():
    ics = cal(
        event_utc("evt-1", "20260105T090000Z", "20260105T100000Z", "SUMMARY:sync"),
        event(
            "UID:evt-2",
            "DTSTART:20260105T120000Z",
            "DTEND:20260105T130000Z",
            "RRULE:FREQ=DAILY;COUNT=3",
        ),
    )
    resp = diff(ics, ics)
    assert resp.status_code == 200
    assert resp.json() == {
        "window_start": "2026-01-01T00:00:00Z",
        "window_end": "2026-02-01T00:00:00Z",
        "added": [],
        "removed": [],
        "modified": [],
    }


def test_window_limits_compared_instances():
    # evt-2 is added, but outside the window it must not show up.
    old = cal(event_utc("evt-1", "20260105T090000Z", "20260105T100000Z"))
    new = cal(
        event_utc("evt-1", "20260105T090000Z", "20260105T100000Z"),
        event_utc("evt-2", "20260301T090000Z", "20260301T100000Z"),
    )
    resp = diff(old, new)
    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] == []
    assert body["removed"] == []
    assert body["modified"] == []


# ---------------------------------------------------------------------------
# modified: start / end / summary / status
# ---------------------------------------------------------------------------

def test_rescheduled_recurring_instance_is_modified_with_before_and_after():
    master = event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",  # Mondays 09:00 UTC
        "DTEND:20260105T100000Z",
        "RRULE:FREQ=WEEKLY;COUNT=3",  # Jan 5, 12, 19
        "SUMMARY:weekly sync",
    )
    old = cal(master)
    new = cal(
        master,
        event(
            "UID:evt-1",
            "RECURRENCE-ID:20260112T090000Z",
            "DTSTART:20260113T100000Z",
            "DTEND:20260113T110000Z",
        ),
    )
    resp = diff(old, new)
    assert resp.status_code == 200
    body = resp.json()
    assert body["added"] == []
    assert body["removed"] == []
    assert len(body["modified"]) == 1
    change = body["modified"][0]
    # Identity (UID + RECURRENCE-ID) is stable across the reschedule.
    assert change["before"]["recurrence_id"] == "2026-01-12T09:00:00Z"
    assert change["after"]["recurrence_id"] == "2026-01-12T09:00:00Z"
    assert change["before"]["start"] == "2026-01-12T09:00:00Z"
    assert change["before"]["end"] == "2026-01-12T10:00:00Z"
    assert change["after"]["start"] == "2026-01-13T10:00:00Z"
    assert change["after"]["end"] == "2026-01-13T11:00:00Z"


def test_summary_change_on_non_recurring_event_is_modified():
    old = cal(event_utc("evt-1", "20260105T090000Z", "20260105T100000Z",
                        "SUMMARY:old title"))
    new = cal(event_utc("evt-1", "20260105T090000Z", "20260105T100000Z",
                        "SUMMARY:new title"))
    resp = diff(old, new)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["modified"]) == 1
    change = body["modified"][0]
    assert change["before"]["uid"] == "evt-1"
    assert change["before"]["recurrence_id"] is None
    assert change["before"]["summary"] == "old title"
    assert change["after"]["summary"] == "new title"


def test_status_change_is_modified():
    old = cal(event_utc("evt-1", "20260105T090000Z", "20260105T100000Z",
                        "STATUS:TENTATIVE"))
    new = cal(event_utc("evt-1", "20260105T090000Z", "20260105T100000Z",
                        "STATUS:CONFIRMED"))
    resp = diff(old, new)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["modified"]) == 1
    change = body["modified"][0]
    assert change["before"]["status"] == "TENTATIVE"
    assert change["after"]["status"] == "CONFIRMED"


def test_end_change_is_modified():
    old = cal(event_utc("evt-1", "20260105T090000Z", "20260105T100000Z"))
    new = cal(event_utc("evt-1", "20260105T090000Z", "20260105T110000Z"))
    resp = diff(old, new)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["modified"]) == 1
    change = body["modified"][0]
    assert change["before"]["end"] == "2026-01-05T10:00:00Z"
    assert change["after"]["end"] == "2026-01-05T11:00:00Z"


def test_cancelled_override_instance_is_removed_not_modified():
    master = event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        "DTEND:20260105T100000Z",
        "RRULE:FREQ=WEEKLY;COUNT=3",
    )
    old = cal(master)
    new = cal(
        master,
        event(
            "UID:evt-1",
            "RECURRENCE-ID:20260112T090000Z",
            "DTSTART:20260112T090000Z",
            "DTEND:20260112T100000Z",
            "STATUS:CANCELLED",
        ),
    )
    resp = diff(old, new)
    assert resp.status_code == 200
    body = resp.json()
    assert identities(body["removed"]) == [("evt-1", "2026-01-12T09:00:00Z")]
    assert body["added"] == []
    assert body["modified"] == []


# ---------------------------------------------------------------------------
# Identity and ordering
# ---------------------------------------------------------------------------

def test_results_sorted_by_identity():
    old = cal(
        event_utc("uid-b", "20260105T090000Z", "20260105T100000Z"),
        event_utc("uid-a", "20260105T090000Z", "20260105T100000Z"),
        event(
            "UID:uid-c",
            "DTSTART:20260105T090000Z",
            "DTEND:20260105T100000Z",
            "RRULE:FREQ=DAILY;COUNT=2",
        ),
    )
    new = cal(
        event_utc("uid-b", "20260105T090000Z", "20260105T100000Z"),
        # uid-a removed; uid-c first occurrence rescheduled, second untouched.
        event(
            "UID:uid-c",
            "DTSTART:20260105T090000Z",
            "DTEND:20260105T100000Z",
            "RRULE:FREQ=DAILY;COUNT=2",
        ),
        event(
            "UID:uid-c",
            "RECURRENCE-ID:20260105T090000Z",
            "DTSTART:20260105T110000Z",
            "DTEND:20260105T120000Z",
        ),
        event_utc("uid-d", "20260107T090000Z", "20260107T100000Z"),
    )
    resp = diff(old, new)
    assert resp.status_code == 200
    body = resp.json()
    assert identities(body["added"]) == [("uid-d", None)]
    assert identities(body["removed"]) == [("uid-a", None)]
    assert modified_identities(body["modified"]) == [("uid-c", "2026-01-05T09:00:00Z")]


def test_recurrence_id_distinguishes_instances_of_same_uid():
    # Same UID, two occurrences; only the second one moves.
    master = event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        "DTEND:20260105T100000Z",
        "RRULE:FREQ=DAILY;COUNT=2",
    )
    old = cal(master)
    new = cal(
        master,
        event(
            "UID:evt-1",
            "RECURRENCE-ID:20260106T090000Z",
            "DTSTART:20260106T140000Z",
            "DTEND:20260106T150000Z",
        ),
    )
    resp = diff(old, new)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["modified"]) == 1
    change = body["modified"][0]
    assert change["before"]["recurrence_id"] == "2026-01-06T09:00:00Z"
    assert change["after"]["start"] == "2026-01-06T14:00:00Z"


# ---------------------------------------------------------------------------
# 422 semantics
# ---------------------------------------------------------------------------

def test_invalid_old_ics_returns_422():
    resp = diff("not a calendar", cal(event_utc("e", "20260105T090000Z",
                                                "20260105T100000Z")))
    assert resp.status_code == 422
    assert "invalid iCalendar" in resp.json()["detail"]


def test_invalid_new_ics_returns_422():
    resp = diff(cal(event_utc("e", "20260105T090000Z", "20260105T100000Z")),
                "not a calendar")
    assert resp.status_code == 422
    assert "invalid iCalendar" in resp.json()["detail"]


def test_invalid_rrule_in_either_calendar_returns_422():
    bad = cal(event(
        "UID:evt-1",
        "DTSTART:20260105T090000Z",
        "RRULE:FREQ=BOGUS;COUNT=2",
    ))
    good = cal(event_utc("evt-1", "20260105T090000Z", "20260105T100000Z"))
    assert diff(bad, good).status_code == 422
    assert diff(good, bad).status_code == 422


def test_inverted_window_returns_422():
    ics = cal(event_utc("evt-1", "20260105T090000Z", "20260105T100000Z"))
    resp = diff(ics, ics, "2026-02-01T00:00:00Z", "2026-01-01T00:00:00Z")
    assert resp.status_code == 422
    assert "window_start" in resp.json()["detail"]
