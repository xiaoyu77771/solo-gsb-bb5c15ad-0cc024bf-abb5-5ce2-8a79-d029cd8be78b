"""Diff two expanded calendars within the same query window.

Instance identity is UID + RECURRENCE-ID; a non-recurring event is
identified by its UID alone (its RECURRENCE-ID is None). An instance
present only in the new calendar is "added", only in the old one
"removed"; present in both with any of start/end/summary/status changed
it is "modified" and carries both versions. Identical instances are
omitted. All three lists are sorted by identity.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .expander import Instance

UTC = dt.timezone.utc

# Sort/sentinel value for instances without a RECURRENCE-ID.
_NO_RID = dt.datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class ModifiedInstance:
    before: Instance  # instance as expanded from the old calendar
    after: Instance  # instance as expanded from the new calendar


def _identity(inst: Instance) -> tuple[str, dt.datetime]:
    return (inst.uid, inst.recurrence_id or _NO_RID)


def _changed(old: Instance, new: Instance) -> bool:
    return (
        old.start != new.start
        or old.end != new.end
        or old.summary != new.summary
        or old.status != new.status
    )


def diff_instances(
    old: list[Instance], new: list[Instance]
) -> tuple[list[Instance], list[Instance], list[ModifiedInstance]]:
    """Compare two expanded instance lists by identity.

    Returns (added, removed, modified), each sorted by identity
    (UID, then RECURRENCE-ID; instances without one sort first).
    """
    old_by_id = {_identity(i): i for i in old}
    new_by_id = {_identity(i): i for i in new}

    added = sorted(
        (i for key, i in new_by_id.items() if key not in old_by_id),
        key=_identity,
    )
    removed = sorted(
        (i for key, i in old_by_id.items() if key not in new_by_id),
        key=_identity,
    )
    modified = sorted(
        (
            ModifiedInstance(before=old_by_id[key], after=new_by_id[key])
            for key in old_by_id.keys() & new_by_id.keys()
            if _changed(old_by_id[key], new_by_id[key])
        ),
        key=lambda m: _identity(m.after),
    )
    return added, removed, modified
