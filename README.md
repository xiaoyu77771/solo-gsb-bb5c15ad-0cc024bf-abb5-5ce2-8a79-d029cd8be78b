# iCalendar Recurrence Expansion & Availability API

A local HTTP API that expands iCalendar (RFC 5545) recurring events into
concrete UTC instances inside a query window, finds the **common free
time** across several named calendars when intersected with per-weekday
local working hours, and **diffs two revisions** of a calendar within one
query window.

- Expands `DTSTART` + `RRULE` + `RDATE` − `EXDATE`
- Merges overrides by `UID` + `RECURRENCE-ID`: an override can **reschedule**
  an instance (judged against the window by its *actual* new start/end; the
  original slot is never kept) or **cancel** it via `STATUS:CANCELLED`
- Interprets `TZID` as IANA timezones with correct DST handling
- `/availability` builds working hours per **local date** (DST-correct),
  clips all busy instances to them, merges half-open intervals (adjacent
  included), and returns the complement shared by every calendar
- `/calendar-diff` expands an old and a new calendar revision in the same
  UTC window and reports instances as `added`, `removed`, or `modified`
  (with before/after), keyed by `UID` + `RECURRENCE-ID`
- Results sorted by UTC start time, then `UID` (stable)
- Clear `422` errors for invalid rules, unknown timezones, or bad input

Stack: Python · FastAPI · icalendar · python-dateutil · zoneinfo

## Quick start (Docker Compose)

Single command to build and start the service:

```bash
docker compose up --build
```

The API listens on **http://localhost:8000** (interactive docs at `/docs`,
health check at `/health`).

## API

### `POST /expand`

Request body:

| Field          | Type   | Description                                        |
| -------------- | ------ | -------------------------------------------------- |
| `ics`          | string | iCalendar text (one or more `VEVENT`s)             |
| `window_start` | string | ISO 8601 timestamp, converted to UTC (naive = UTC) |
| `window_end`   | string | ISO 8601 timestamp, converted to UTC (naive = UTC) |

An instance is returned when it **overlaps** `[window_start, window_end)`
(a zero-duration event must start inside the window). Each instance carries
`uid`, `recurrence_id` (UTC, `null` for non-recurring events), `summary`,
`start`, `end`, and `status`; all timestamps are UTC (`Z`).

### Example

```bash
curl -s http://localhost:8000/expand \
  -H 'Content-Type: application/json' \
  -d '{
    "ics": "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//demo//EN\r\nBEGIN:VEVENT\r\nUID:weekly-1\r\nDTSTART;TZID=America/New_York:20260301T090000\r\nDTEND;TZID=America/New_York:20260301T100000\r\nRRULE:FREQ=WEEKLY;COUNT=3\r\nEXDATE;TZID=America/New_York:20260308T090000\r\nSUMMARY:Weekly sync\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:weekly-1\r\nRECURRENCE-ID;TZID=America/New_York:20260315T090000\r\nDTSTART;TZID=America/New_York:20260316T110000\r\nDTEND;TZID=America/New_York:20260316T120000\r\nSUMMARY:Weekly sync (moved)\r\nEND:VEVENT\r\nEND:VCALENDAR",
    "window_start": "2026-02-25T00:00:00Z",
    "window_end": "2026-04-01T00:00:00Z"
  }'
```

Response — note the DST shift (14:00Z → 13:00Z), the `EXDATE`-removed
March 8 occurrence, and the March 15 instance replaced by its override at
the new time (the original slot is gone):

```json
{
  "window_start": "2026-02-25T00:00:00Z",
  "window_end": "2026-04-01T00:00:00Z",
  "count": 2,
  "instances": [
    {
      "uid": "weekly-1",
      "recurrence_id": "2026-03-01T14:00:00Z",
      "summary": "Weekly sync",
      "start": "2026-03-01T14:00:00Z",
      "end": "2026-03-01T15:00:00Z",
      "status": "CONFIRMED"
    },
    {
      "uid": "weekly-1",
      "recurrence_id": "2026-03-15T13:00:00Z",
      "summary": "Weekly sync (moved)",
      "start": "2026-03-16T15:00:00Z",
      "end": "2026-03-16T16:00:00Z",
      "status": "CONFIRMED"
    }
  ]
}
```

### `POST /availability`

Finds free slots that lie inside the working hours **and** avoid busy
instances in *every* supplied calendar. Request body:

| Field             | Type                | Description                                                       |
| ----------------- | ------------------- | ----------------------------------------------------------------- |
| `calendars`       | object              | Map of name → iCalendar text; may be empty or omitted             |
| `window_start`    | string              | ISO 8601 timestamp, converted to UTC (naive = UTC)                |
| `window_end`      | string              | ISO 8601 timestamp, converted to UTC (naive = UTC)                |
| `timezone`        | string              | IANA timezone in which `working_hours` are interpreted            |
| `working_hours`   | object              | Keys `monday`…`sunday`; each a list of `{start, end}` local times |
| `minimum_minutes` | integer             | Only free slots at least this long are returned (must be > 0)     |

Working periods are generated for each local date in the window and then
converted to UTC, so a fixed local time shifts correctly across DST
transitions. A missing weekday (or an empty list) means no working time
that day; multiple periods per day are supported (e.g. a lunch break).
Periods must not be inverted (`end <= start`) or cross midnight.

Busy time is every expanded instance that is not `STATUS:CANCELLED` and
has a positive duration; instances are clipped to the working periods and
merged as half-open `[start, end)` intervals, with touching intervals
merged (back-to-back meetings leave no free instant between them). The
complement inside the working periods is returned, sorted by `start`,
keeping only slots of at least `minimum_minutes` (inclusive at the
boundary). Each slot carries UTC `start`, `end`, and integer `minutes`.

### Example

```bash
curl -s http://localhost:8000/availability \
  -H 'Content-Type: application/json' \
  -d '{
    "calendars": {
      "alice": "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//demo//EN\r\nBEGIN:VEVENT\r\nUID:a1\r\nDTSTART;TZID=Europe/Berlin:20260309T100000\r\nDTEND;TZID=Europe/Berlin:20260309T110000\r\nSUMMARY:Dentist\r\nEND:VEVENT\r\nEND:VCALENDAR",
      "bob": "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//demo//EN\r\nBEGIN:VEVENT\r\nUID:b1\r\nDTSTART:20260309T140000Z\r\nDTEND:20260309T153000Z\r\nSUMMARY:Interview\r\nEND:VEVENT\r\nEND:VCALENDAR"
    },
    "window_start": "2026-03-09T00:00:00Z",
    "window_end": "2026-03-10T00:00:00Z",
    "timezone": "Europe/Berlin",
    "working_hours": {
      "monday": [
        {"start": "09:00", "end": "12:00"},
        {"start": "13:00", "end": "17:00"}
      ]
    },
    "minimum_minutes": 30
  }'
```

Berlin is UTC+1 on this date, so the working periods are `08:00–11:00Z`
and `12:00–16:00Z`; Alice blocks `09:00–10:00Z` and Bob `14:00–15:30Z`.
The lunch break is not free time, and the final 30-minute tail is kept
because the minimum length is inclusive:

```json
{
  "window_start": "2026-03-09T00:00:00Z",
  "window_end": "2026-03-10T00:00:00Z",
  "timezone": "Europe/Berlin",
  "count": 4,
  "slots": [
    {"start": "2026-03-09T08:00:00Z", "end": "2026-03-09T09:00:00Z", "minutes": 60},
    {"start": "2026-03-09T10:00:00Z", "end": "2026-03-09T11:00:00Z", "minutes": 60},
    {"start": "2026-03-09T12:00:00Z", "end": "2026-03-09T14:00:00Z", "minutes": 120},
    {"start": "2026-03-09T15:30:00Z", "end": "2026-03-09T16:00:00Z", "minutes": 30}
  ]
}
```

### `POST /calendar-diff`

Compares two revisions of a calendar. Both ICS texts are expanded with the
same recurrence engine inside one shared UTC query window, and the
resulting instances are matched by identity: `UID` + `RECURRENCE-ID` for
recurring instances, `UID` alone for non-recurring events. Request body:

| Field          | Type   | Description                                        |
| -------------- | ------ | -------------------------------------------------- |
| `old_ics`      | string | iCalendar text of the old revision                 |
| `new_ics`      | string | iCalendar text of the new revision                 |
| `window_start` | string | ISO 8601 timestamp, converted to UTC (naive = UTC) |
| `window_end`   | string | ISO 8601 timestamp, converted to UTC (naive = UTC) |

The response carries three lists, each sorted by identity (`UID`, then
`RECURRENCE-ID`):

- `added` — instances whose identity exists only in the new revision
- `removed` — instances whose identity exists only in the old revision
  (e.g. an occurrence cancelled via a `STATUS:CANCELLED` override)
- `modified` — instances present in both revisions where `start`, `end`,
  `summary`, or `status` changed; each entry carries the full `before`
  and `after` instance

Instances identical in both revisions are not returned. Only instances
overlapping the window are compared, so changes outside the window do not
appear. Note that a rescheduled recurring instance keeps its identity
(`RECURRENCE-ID` follows the original slot), so it shows up as `modified`
with different `start`/`end` — not as a remove/add pair.

### Example

```bash
curl -s http://localhost:8000/calendar-diff \
  -H 'Content-Type: application/json' \
  -d '{
    "old_ics": "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//demo//EN\r\nBEGIN:VEVENT\r\nUID:weekly-1\r\nDTSTART:20260302T090000Z\r\nDTEND:20260302T100000Z\r\nRRULE:FREQ=WEEKLY;COUNT=2\r\nSUMMARY:Weekly sync\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:one-off-1\r\nDTSTART:20260304T150000Z\r\nDTEND:20260304T160000Z\r\nSUMMARY:Review\r\nEND:VEVENT\r\nEND:VCALENDAR",
    "new_ics": "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//demo//EN\r\nBEGIN:VEVENT\r\nUID:weekly-1\r\nDTSTART:20260302T090000Z\r\nDTEND:20260302T100000Z\r\nRRULE:FREQ=WEEKLY;COUNT=2\r\nSUMMARY:Weekly sync\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:weekly-1\r\nRECURRENCE-ID:20260309T090000Z\r\nDTSTART:20260309T110000Z\r\nDTEND:20260309T120000Z\r\nSUMMARY:Weekly sync (moved)\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:one-off-2\r\nDTSTART:20260305T150000Z\r\nDTEND:20260305T160000Z\r\nSUMMARY:Planning\r\nEND:VEVENT\r\nEND:VCALENDAR",
    "window_start": "2026-03-01T00:00:00Z",
    "window_end": "2026-04-01T00:00:00Z"
  }'
```

The March 9 occurrence was rescheduled (same `RECURRENCE-ID`, new time),
`one-off-1` disappeared and `one-off-2` is new:

```json
{
  "window_start": "2026-03-01T00:00:00Z",
  "window_end": "2026-04-01T00:00:00Z",
  "added": [
    {
      "uid": "one-off-2",
      "recurrence_id": null,
      "summary": "Planning",
      "start": "2026-03-05T15:00:00Z",
      "end": "2026-03-05T16:00:00Z",
      "status": "CONFIRMED"
    }
  ],
  "removed": [
    {
      "uid": "one-off-1",
      "recurrence_id": null,
      "summary": "Review",
      "start": "2026-03-04T15:00:00Z",
      "end": "2026-03-04T16:00:00Z",
      "status": "CONFIRMED"
    }
  ],
  "modified": [
    {
      "before": {
        "uid": "weekly-1",
        "recurrence_id": "2026-03-09T09:00:00Z",
        "summary": "Weekly sync",
        "start": "2026-03-09T09:00:00Z",
        "end": "2026-03-09T10:00:00Z",
        "status": "CONFIRMED"
      },
      "after": {
        "uid": "weekly-1",
        "recurrence_id": "2026-03-09T09:00:00Z",
        "summary": "Weekly sync (moved)",
        "start": "2026-03-09T11:00:00Z",
        "end": "2026-03-09T12:00:00Z",
        "status": "CONFIRMED"
      }
    }
  ]
}
```

### Errors (`422 Unprocessable Entity`)

Returned with a descriptive `detail` message for: unparsable iCalendar
text, invalid `RRULE` (e.g. `FREQ=BOGUS`), unknown `TZID`
(non-IANA name), missing `DTSTART`/`UID`, `RECURRENCE-ID` with
`RANGE=THISANDFUTURE` (unsupported), `window_start >= window_end`, and
expansions exceeding 10 000 occurrences per UID within the window.

`/availability` additionally returns `422` for: an unknown `timezone`
(non-IANA name), a working period with `end <= start` or crossing
midnight, an offset-bearing working time, an unknown weekday key, and a
non-positive `minimum_minutes`.

`/calendar-diff` applies the same `422` semantics: either calendar being
invalid, or `window_start >= window_end`, produces a `422` with a
descriptive `detail` message.

## Semantics and limitations

- **Window overlap**: an instance is included when
  `start < window_end and end > window_start`; zero-duration instances when
  `window_start <= start < window_end`.
- **Overrides** replace the occurrence identified by `RECURRENCE-ID` even
  when the original falls outside the expansion window (e.g. moved into the
  window from outside). A `STATUS:CANCELLED` override removes the instance.
  An override without `DTEND`/`DURATION` inherits the master's duration.
- **Floating times** (no `Z`, no `TZID`) are interpreted as UTC.
- **All-day events** (`DATE`-valued `DTSTART`) start at local midnight and
  last one day when no `DTEND` is given.
- `RDATE`/`EXDATE` support `DATE` and `DATE-TIME` values (not `PERIOD`);
  `RECURRENCE-ID` with `RANGE=THISANDFUTURE` is not supported.
- A master event with `STATUS:CANCELLED` suppresses its whole series.

### Availability semantics

- **Working hours are local**: each weekday's periods are materialized per
  local date with `zoneinfo` and converted to UTC, so local times shift at
  DST transitions (the UTC timeline is built day by day — never by a fixed
  offset). Periods are clipped to the query window.
- **Busy intervals** are the expanded instances of every calendar,
  excluding `STATUS:CANCELLED` and non-positive-duration instances. Each is
  intersected with the working periods before merging, so events outside
  working time never remove free slots.
- **Half-open intervals** `[start, end)` are used throughout: two busy
  intervals that touch at an instant merge, and a busy event ending exactly
  when a free slot begins does not consume it.
- **Common free time** is the complement of the merged union of *all*
  calendars' busy intervals within the merged working periods; gaps between
  a day's working periods (e.g. lunch) and non-working weekdays are never
  returned.
- `minimum_minutes` is applied to slot length and is inclusive at the
  boundary; `minutes` is the integer UTC duration. Slots are sorted by
  `start` and serialized with the `Z` suffix.
- Working periods crossing local midnight are not supported (a range must
  start and end on the same local date).

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v          # run the test suite
uvicorn app.main:app --reload       # run locally on port 8000
```

## Project layout

```
app/main.py          FastAPI app: POST /expand, POST /availability, POST /calendar-diff, GET /health
app/expander.py      recurrence expansion, override merging, TZ/DST handling
app/availability.py  working-period timeline, busy clipping/merge, free complement
app/diff.py          identity-keyed comparison of two expanded calendars
tests/               key tests for all endpoints (recurrences, DST, diff, 422s)
Dockerfile           python:3.12-slim image running uvicorn on port 8000
docker-compose.yml   single-command startup
```
