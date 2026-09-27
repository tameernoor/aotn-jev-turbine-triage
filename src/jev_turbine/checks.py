"""Per-turbine and farm-wide code checks over a start-ordered event list.

Pure functions, no Jev: `chattering` (the same message starting 3 or more times
within 10 minutes on one turbine), `floods` (more than 10 non-informational events
starting within 10 minutes anywhere on the farm) and `long_stops` (a Stop lasting
more than 24 hours). All three return the indices into the given `events` sequence
that the check flags; they assume `events` is already sorted by start time, which is
what `load_events` returns.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timedelta

from .models import Event

CHATTER_WINDOW = timedelta(minutes=10)
CHATTER_THRESHOLD = 3
FLOOD_WINDOW = timedelta(minutes=10)
FLOOD_THRESHOLD = 11  # "more than 10"
LONG_STOP_SECONDS = 24 * 3600


def chattering(events: Sequence[Event]) -> set[int]:
    groups: dict[tuple[str, str], list[tuple[datetime, int]]] = defaultdict(list)
    for i, event in enumerate(events):
        groups[(event.turbine, event.message)].append((event.start, i))
    flagged: set[int] = set()
    for pairs in groups.values():
        flagged |= _flag_dense_runs(pairs, CHATTER_WINDOW, CHATTER_THRESHOLD)
    return flagged


def floods(events: Sequence[Event]) -> set[int]:
    pairs = [(e.start, i) for i, e in enumerate(events) if e.status != "Informational"]
    return _flag_dense_runs(pairs, FLOOD_WINDOW, FLOOD_THRESHOLD)


def long_stops(events: Sequence[Event]) -> set[int]:
    return {
        i
        for i, e in enumerate(events)
        if e.status == "Stop"
        and e.duration_seconds is not None
        and e.duration_seconds > LONG_STOP_SECONDS
    }


def _flag_dense_runs(pairs: list[tuple[datetime, int]], window: timedelta, threshold: int) -> set[int]:
    """Mark every item in some run of >= threshold items that all start within
    `window` of the run's first item. `pairs` is (start, original index); it is
    sorted here, so callers need not pre-sort each group."""
    pairs = sorted(pairs, key=lambda p: p[0])
    flagged: set[int] = set()
    n = len(pairs)
    j = 0
    for i in range(n):
        if j < i:
            j = i
        while j < n and pairs[j][0] < pairs[i][0] + window:
            j += 1
        if j - i >= threshold:
            flagged.update(idx for _, idx in pairs[i:j])
    return flagged
