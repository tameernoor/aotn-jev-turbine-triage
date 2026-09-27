"""Builds plain-text context for one or more events, from their own turbines'
10-minute measurements (measurements.py, sql/context.sql) and the full event list
(loader.py). A later step sends the rendered text to Jev alongside `status` and
`message` when step 1 is uncertain about an event.

The measurement numbers (before/after means, the below-50kW duration, rotor, grid
min/max) come from ONE SQL query over every event passed in at once
(`measurement_stats`): pass the whole batch of events to escalate to
`measurement_stats`/`build_contexts` in a single call, not one call per event.
Everything else here (rendering, and the three history lines) is plain Python over
the in-memory event list.

Window rules:

- 10-minute rows mark the START of their period: a row stamped ts covers
  [ts, ts+10min). The row whose period contains the event start straddles it and
  is excluded from both the before and after mean. Before is the 6 rows (a full
  60 minutes) ending at that row's start; after is the 6 rows starting at that
  row's end. If the event start falls exactly on the 10-minute grid there is no
  straddling row: before is the 6 rows ending at the start, after is the 6 rows
  starting at it. See `_grid_boundaries`.
- "Around" (grid frequency/voltage): the straddling row (or the row at the start,
  on the grid) plus 3 rows before and 3 after, 7 rows in total. Still rendered as
  "the hour around the event".
- "Farm-wide stop": counts distinct OTHER turbines with a Stop starting within 10
  minutes either side of this event's start. This is a choice, not a rule from
  the source data: "the same 10 minutes" is read generously, both directions.
- "Power stayed below 50 kW": walks forward from the event start over known
  (non-NULL) power readings. The stretch from the event start to the first known
  reading is never itself treated as a gap, however late that reading is: only a
  gap of more than 30 minutes BETWEEN two known readings counts as the data
  stopping. A reading that follows such a gap is never a recovery (or "did not
  drop"), even if its own power is already >= 50 kW; the gap resolves the search
  first. So: the first known reading is already >= 50 kW -> it did not drop (0);
  a later reading is >= 50 kW with no gap before it -> exact seconds to that
  reading; readings stay known and low, gap-free, all the way to 24h -> "at
  least 24 h"; a gap happens first -> "at least <X>, then no power data", X to
  the end of the last known reading before the gap. Missing data is said, not
  guessed.

Rotor speed has no rounding rule in the plan; rounds to the nearest whole RPM,
matching the plan's own "0 RPM" example.

"Other turbines stopped" always renders (yes/no+count); "event just before" only
renders when one exists; the whole measurement block collapses to one "No data"
line only when both before and after power are missing.

DuckDB's Python client here cannot fetch a raw TIMESTAMPTZ value back into Python.
Event start times are only ever bound INTO DuckDB as query parameters; results
crossing back to Python are plain numbers, matched to events by a positional
index, never by timestamp.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from .models import Event

_SQL_PATH = Path(__file__).resolve().parent / "sql" / "context.sql"
_CONTEXT_SQL = _SQL_PATH.read_text(encoding="utf-8")

HISTORY_WINDOW = timedelta(days=7)
JUST_BEFORE_WINDOW = timedelta(minutes=30)
FARM_WIDE_WINDOW = timedelta(minutes=10)
GRID_STEP = timedelta(minutes=10)

LOW_POWER_THRESHOLD_KW = 50.0

NO_DATA_LINE = "No 10-minute data around this event."


@dataclass(frozen=True)
class MeasurementStats:
    """One event's measurement numbers, as computed by sql/context.sql. `None`
    means "no data in that window", not zero."""

    before_power: float | None
    before_wind: float | None
    after_power: float | None
    rotor_after: float | None
    freq_min: float | None
    freq_max: float | None
    volt_min: float | None
    volt_max: float | None
    low_power_recovered_seconds: float | None
    low_power_gap_seconds: float | None


def measurement_stats(con: duckdb.DuckDBPyConnection, events: Sequence[Event]) -> list[MeasurementStats]:
    """Run sql/context.sql once for every event in `events`, returned in the same
    order. This is the batch entry point Task 2's escalation step should call
    with the whole set of events to escalate in one go; `build_context` below is
    a single-event convenience wrapper over the same batch machinery."""
    if not events:
        return []
    con.execute(
        "CREATE OR REPLACE TEMP TABLE _context_events "
        "(idx INTEGER, turbine INTEGER, start TIMESTAMPTZ, before_end TIMESTAMPTZ, after_start TIMESTAMPTZ)"
    )
    rows = []
    for i, event in enumerate(events):
        before_end, after_start = _grid_boundaries(event.start)
        rows.append((i, _turbine_number(event.turbine), event.start, before_end, after_start))
    con.executemany("INSERT INTO _context_events VALUES (?, ?, ?, ?, ?)", rows)
    result_rows = con.execute(_CONTEXT_SQL).fetchall()
    by_idx = {row[0]: row[1:] for row in result_rows}
    return [MeasurementStats(*by_idx[i]) for i in range(len(events))]


def build_contexts(events: Sequence[Event], con: duckdb.DuckDBPyConnection, all_events: Sequence[Event]) -> list[str]:
    """Render context text for every event in `events` (typically the events
    selected for escalation), one SQL query total. `all_events` is the full,
    all-turbine event list the history lines are computed from."""
    stats = measurement_stats(con, events)
    return [
        "\n".join(_measurement_lines(s) + _history_lines(event, all_events))
        for event, s in zip(events, stats)
    ]


def build_context(event: Event, con: duckdb.DuckDBPyConnection, events: Sequence[Event]) -> str:
    """Render the full plain-text context for one event: its own turbine's
    10-minute measurements around the event, then the event-history lines.
    `events` is the full, all-turbine event list (see loader.load_events)."""
    [text] = build_contexts([event], con, events)
    return text


def _turbine_number(turbine_name: str) -> int:
    digits = "".join(ch for ch in turbine_name if ch.isdigit())
    if not digits:
        raise ValueError(f"can't find a turbine number in {turbine_name!r}")
    return int(digits)


def _grid_boundaries(start: datetime) -> tuple[datetime, datetime]:
    """(before_end, after_start): before is the 6 rows ending at before_end;
    after is the 6 rows starting at after_start. If `start` is exactly on the
    10-minute grid, both equal `start`. Otherwise the row covering `start`
    straddles it (excluded from both): before_end is that row's own start,
    after_start is ten minutes later, its end."""
    floor = start.replace(minute=(start.minute // 10) * 10, second=0, microsecond=0)
    if floor == start:
        return start, start
    return floor, floor + GRID_STEP


# --- measurements ---------------------------------------------------------------


def _measurement_lines(stats: MeasurementStats) -> list[str]:
    if stats.before_power is None and stats.after_power is None:
        return [NO_DATA_LINE]

    lines: list[str] = []

    before_parts = []
    if stats.before_power is not None:
        before_parts.append(f"power {_fmt_power(stats.before_power)}")
    if stats.before_wind is not None:
        before_parts.append(f"wind {_fmt_wind(stats.before_wind)}")
    if before_parts:
        lines.append(f"Before the event: {', '.join(before_parts)} (60-minute averages).")

    if stats.after_power is not None:
        lines.append(
            f"After the event: power {_fmt_power(stats.after_power)}; "
            f"power stayed below {int(LOW_POWER_THRESHOLD_KW)} kW for {_fmt_low_power_duration(stats)}."
        )

    if stats.rotor_after is not None:
        lines.append(f"Rotor after the event: {_fmt_rotor(stats.rotor_after)}.")

    grid_parts = []
    if stats.freq_min is not None:
        grid_parts.append(f"frequency {_fmt_freq(stats.freq_min)} to {_fmt_freq(stats.freq_max)} Hz")
    if stats.volt_min is not None:
        grid_parts.append(f"voltage {_fmt_voltage(stats.volt_min)} to {_fmt_voltage(stats.volt_max)} V")
    if grid_parts:
        lines.append(f"Grid in the hour around the event: {', '.join(grid_parts)}.")

    return lines


def _fmt_low_power_duration(stats: MeasurementStats) -> str:
    if stats.low_power_recovered_seconds is not None:
        return _fmt_duration(timedelta(seconds=stats.low_power_recovered_seconds))
    if stats.low_power_gap_seconds is not None:
        duration = _fmt_duration(timedelta(seconds=stats.low_power_gap_seconds))
        return f"at least {duration}, then no power data"
    return "at least 24 h"


# --- history ---------------------------------------------------------------------


def _history_lines(event: Event, events: Sequence[Event]) -> list[str]:
    lines = [
        f"Same message on this turbine in the previous 7 days: {_same_message_count(event, events)} times."
    ]

    just_before = _event_just_before(event, events)
    if just_before is not None:
        lines.append(f"Event just before on this turbine (within 30 min): {just_before}.")

    stop_count = _farm_wide_stop_count(event, events)
    if stop_count:
        lines.append(f"Other turbines stopped in the same 10 minutes: yes ({stop_count}).")
    else:
        lines.append("Other turbines stopped in the same 10 minutes: no.")

    return lines


def _same_message_count(event: Event, events: Sequence[Event]) -> int:
    """Starts strictly before this event's start, in the 7 days before it."""
    window_start = event.start - HISTORY_WINDOW
    return sum(
        1
        for e in events
        if e.turbine == event.turbine and e.message == event.message and window_start <= e.start < event.start
    )


def _event_just_before(event: Event, events: Sequence[Event]) -> str | None:
    """The nearest non-informational event on the same turbine starting in the 30
    minutes before this one, if any."""
    window_start = event.start - JUST_BEFORE_WINDOW
    candidates = [
        e
        for e in events
        if e is not event
        and e.turbine == event.turbine
        and e.status != "Informational"
        and window_start <= e.start < event.start
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda e: e.start).message


def _farm_wide_stop_count(event: Event, events: Sequence[Event]) -> int:
    """Distinct other turbines with a Stop starting within 10 minutes either side
    of this event's start (not a count of Stop events: one turbine chattering
    through several Stops in the window still counts once)."""
    window_start = event.start - FARM_WIDE_WINDOW
    window_end = event.start + FARM_WIDE_WINDOW
    turbines = {
        e.turbine
        for e in events
        if e.turbine != event.turbine and e.status == "Stop" and window_start <= e.start <= window_end
    }
    return len(turbines)


# --- rendering ---------------------------------------------------------------------


def _round_to(value: float, step: float) -> float:
    return round(value / step) * step


def _fmt_power(value: float) -> str:
    return f"{_round_to(value, 10):,.0f} kW"


def _fmt_wind(value: float) -> str:
    return f"{_round_to(value, 0.1):.1f} m/s"


def _fmt_rotor(value: float) -> str:
    return f"{round(value):.0f} RPM"


def _fmt_freq(value: float) -> str:
    return f"{_round_to(value, 0.01):.2f}"


def _fmt_voltage(value: float) -> str:
    return f"{_round_to(value, 1):.0f}"


def _fmt_duration(delta: timedelta) -> str:
    total_minutes = round(delta.total_seconds() / 600) * 10
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours} h {minutes} min"
    if hours:
        return f"{hours} h"
    return f"{minutes} min"
