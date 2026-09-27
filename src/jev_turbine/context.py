"""Builds plain-text context for one or more events, from their own turbines'
10-minute measurements (queried from DuckDB, see measurements.py and
sql/context.sql) and the full event list (loader.py). A later step sends the
rendered text to Jev alongside `status` and `message` when step 1 is uncertain
about an event.

The measurement numbers (before/after means, the below-50kW duration, rotor,
grid min/max) come from ONE SQL query over every event passed in at once
(`measurement_stats`), not one query per event: pass the whole batch of events to
escalate to `measurement_stats`/`build_contexts` in a single call. Everything else
here (rendering, and the three history lines) is plain Python over the in-memory
event list, no SQL involved.

Window definitions. The plan gives the rules but not the exact boundaries, so they
are pinned down here (and in sql/context.sql, which does the actual filtering):

- 10-minute rows mark the START of their period: a row stamped ts covers
  [ts, ts+10min). "Before" = rows whose whole period ends at or before the event
  start; "after" = rows whose whole period starts at or after the event start. A
  row whose period straddles the start (starts before it, ends after it) counts
  toward neither the before nor the after mean, since it blends pre- and
  post-event readings and would otherwise quietly drag one side's average toward
  the other.
- "Before": the 60 minutes fully before the start. "After": the 60 minutes fully
  after the start.
- "Around" (grid frequency/voltage): the one hour fully within +/-30 minutes of
  the start, i.e. "the hour around the event" in the rendered example is 60
  minutes total, not 120.
- "Farm-wide stop": another turbine logged a Stop whose own start falls within 10
  minutes either side of this event's start (|other.start - event.start| <=
  10min), reading "the same 10 minutes" generously in both directions, as the
  plan allows.
- "Power stayed below 50 kW": measured from the event start, over rows with a
  known (non-None) power reading, ignoring gaps, looking arbitrarily far ahead
  (not just the 60-minute after-window) to find a recovery. If the first known
  reading after the start is already at or above 50 kW, power did not drop and
  the duration is zero. Otherwise the duration runs until the first known reading
  at or above 50 kW within 24 hours of the start; if none is found in that window
  (no recovery seen, or the data does not reach that far), the duration is
  reported as the 24-hour cap itself.

Rotor speed has no rounding rule in the plan (only power, wind, frequency, voltage
and durations are given one); the plan's own worked example renders "0 RPM" with
no decimal, so this rounds rotor speed to the nearest whole RPM.

The "Other turbines stopped in the same 10 minutes" line always renders, as
yes/no(+count), since it is always answerable. "Event just before" only renders
when one exists, and the whole measurement block collapses to the one "No data"
line only when both before and after power are missing, per the plan.

DuckDB's Python client in this environment cannot fetch a raw TIMESTAMPTZ value
back into a Python object (it needs `pytz`, not a project dependency). Nothing
here ever does that: event start times are only ever bound INTO DuckDB as query
parameters, never read back out; sql/context.sql returns plain numbers, matched
back to events by a positional index, not by timestamp.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import duckdb

from .models import Event

_SQL_PATH = Path(__file__).resolve().parent / "sql" / "context.sql"
_CONTEXT_SQL = _SQL_PATH.read_text(encoding="utf-8")

HISTORY_WINDOW = timedelta(days=7)
JUST_BEFORE_WINDOW = timedelta(minutes=30)
FARM_WIDE_WINDOW = timedelta(minutes=10)

LOW_POWER_THRESHOLD_KW = 50.0
LOW_POWER_CAP = timedelta(hours=24)

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
    low_power_seconds: float | None


def measurement_stats(con: duckdb.DuckDBPyConnection, events: Sequence[Event]) -> list[MeasurementStats]:
    """Run sql/context.sql once for every event in `events`, returned in the same
    order. This is the batch entry point Task 2's escalation step should call
    with the whole set of events to escalate in one go; `build_context` below is
    a single-event convenience wrapper over the same batch machinery."""
    if not events:
        return []
    con.execute("CREATE OR REPLACE TEMP TABLE _context_events (idx INTEGER, turbine INTEGER, start TIMESTAMPTZ)")
    con.executemany(
        "INSERT INTO _context_events VALUES (?, ?, ?)",
        [(i, _turbine_number(event.turbine), event.start) for i, event in enumerate(events)],
    )
    rows = con.execute(_CONTEXT_SQL).fetchall()
    by_idx = {row[0]: row[1:] for row in rows}
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
        duration = timedelta(seconds=stats.low_power_seconds)
        lines.append(
            f"After the event: power {_fmt_power(stats.after_power)}; "
            f"power stayed below {int(LOW_POWER_THRESHOLD_KW)} kW for {_fmt_duration(duration)}."
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
    window_start = event.start - FARM_WIDE_WINDOW
    window_end = event.start + FARM_WIDE_WINDOW
    return sum(
        1
        for e in events
        if e.turbine != event.turbine and e.status == "Stop" and window_start <= e.start <= window_end
    )


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
