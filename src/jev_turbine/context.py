"""Builds plain-text context for one event, from that turbine's 10-minute
measurements (measurements.py) and the full event list (loader.py). Pure functions,
no I/O, no Jev: a later step sends the rendered text to Jev alongside `status` and
`message` when step 1 is uncertain about an event.

Window definitions. The plan gives the rules but not the exact boundaries, so they
are pinned down here:

- "Before": 10-minute rows whose END timestamp T falls in (start - 60min, start].
  A row's timestamp is when its 10-minute period finished, so a period that
  finished at or before the event start counts as "before" data, even though its
  last few minutes might already show the event's effect (the real Kelmarsh 1
  example this was checked against has exactly that: a Stop starting at 16:51:17
  already shows a lower power reading in the row stamped 16:50:00).
- "After": rows whose end T falls in (start, start + 60min].
- "Around" (grid frequency/voltage): rows whose end T falls in
  (start - 30min, start + 30min], i.e. the one hour centred on the start ("the hour
  around the event" in the rendered example is 60 minutes total, not 120).
- "Farm-wide stop": another turbine logged a Stop whose own start falls within 10
  minutes either side of this event's start (|other.start - event.start| <= 10min),
  reading "the same 10 minutes" generously in both directions, as the plan allows.
- "Power stayed below 50 kW": measured from the event start, over rows with a known
  (non-None) power reading, ignoring gaps. If the first known reading after the
  start is already at or above 50 kW, power did not drop and the duration is zero.
  Otherwise the duration runs until the first known reading at or above 50 kW within
  24 hours of the start; if none is found in that window (no recovery seen, or the
  data does not reach that far), the duration is reported as the 24-hour cap itself.

Rotor speed has no rounding rule in the plan (only power, wind, frequency, voltage
and durations are given one); the plan's own worked example renders "0 RPM" with no
decimal, so this rounds rotor speed to the nearest whole RPM.

The "Other turbines stopped in the same 10 minutes" line always renders, as
yes/no(+count), since it is always answerable. "Event just before" only renders
when one exists, and the whole measurement block collapses to the one "No data"
line only when both before and after power are missing, per the plan.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from .measurements import MeasurementRow
from .models import Event

BEFORE_WINDOW = timedelta(minutes=60)
AFTER_WINDOW = timedelta(minutes=60)
AROUND_HALF_WINDOW = timedelta(minutes=30)
FARM_WIDE_WINDOW = timedelta(minutes=10)
HISTORY_WINDOW = timedelta(days=7)
JUST_BEFORE_WINDOW = timedelta(minutes=30)

LOW_POWER_THRESHOLD_KW = 50.0
LOW_POWER_CAP = timedelta(hours=24)

POWER = "Power (kW)"
WIND = "Wind speed (m/s)"
ROTOR = "Rotor speed (RPM)"
FREQUENCY = "Grid frequency (Hz)"
VOLTAGE = "Grid voltage (V)"

NO_DATA_LINE = "No 10-minute data around this event."


def build_context(event: Event, turbine_rows: Sequence[MeasurementRow], events: Sequence[Event]) -> str:
    """Render the full plain-text context for one event: its own turbine's 10-minute
    measurements around the event, then the event-history lines. `turbine_rows` must
    already be that one turbine's rows (see measurements.load_measurements), time
    sorted; `events` is the full, all-turbine event list (see loader.load_events)."""
    lines = _measurement_lines(event, turbine_rows) + _history_lines(event, events)
    return "\n".join(lines)


# --- measurements ---------------------------------------------------------------


def _measurement_lines(event: Event, turbine_rows: Sequence[MeasurementRow]) -> list[str]:
    before_rows = _rows_in(turbine_rows, event.start - BEFORE_WINDOW, event.start)
    after_rows = _rows_in(turbine_rows, event.start, event.start + AFTER_WINDOW)
    around_rows = _rows_in(turbine_rows, event.start - AROUND_HALF_WINDOW, event.start + AROUND_HALF_WINDOW)

    before_power = _mean(before_rows, POWER)
    before_wind = _mean(before_rows, WIND)
    after_power = _mean(after_rows, POWER)

    if before_power is None and after_power is None:
        return [NO_DATA_LINE]

    lines: list[str] = []

    before_parts = []
    if before_power is not None:
        before_parts.append(f"power {_fmt_power(before_power)}")
    if before_wind is not None:
        before_parts.append(f"wind {_fmt_wind(before_wind)}")
    if before_parts:
        lines.append(f"Before the event: {', '.join(before_parts)} (60-minute averages).")

    if after_power is not None:
        low_duration = _below_threshold_duration(event.start, turbine_rows)
        lines.append(
            f"After the event: power {_fmt_power(after_power)}; "
            f"power stayed below {int(LOW_POWER_THRESHOLD_KW)} kW for {_fmt_duration(low_duration)}."
        )

    rotor_after = _mean(after_rows, ROTOR)
    if rotor_after is not None:
        lines.append(f"Rotor after the event: {_fmt_rotor(rotor_after)}.")

    freq_min, freq_max = _min_max(around_rows, FREQUENCY)
    volt_min, volt_max = _min_max(around_rows, VOLTAGE)
    grid_parts = []
    if freq_min is not None:
        grid_parts.append(f"frequency {_fmt_freq(freq_min)} to {_fmt_freq(freq_max)} Hz")
    if volt_min is not None:
        grid_parts.append(f"voltage {_fmt_voltage(volt_min)} to {_fmt_voltage(volt_max)} V")
    if grid_parts:
        lines.append(f"Grid in the hour around the event: {', '.join(grid_parts)}.")

    return lines


def _rows_in(rows: Sequence[MeasurementRow], after: datetime, up_to: datetime) -> list[MeasurementRow]:
    """Rows whose end timestamp T satisfies `after` < T <= `up_to`."""
    return [r for r in rows if after < r.timestamp <= up_to]


def _mean(rows: Sequence[MeasurementRow], column: str) -> float | None:
    values = [r.values[column] for r in rows if r.values.get(column) is not None]
    if not values:
        return None
    return sum(values) / len(values)


def _min_max(rows: Sequence[MeasurementRow], column: str) -> tuple[float | None, float | None]:
    values = [r.values[column] for r in rows if r.values.get(column) is not None]
    if not values:
        return None, None
    return min(values), max(values)


def _below_threshold_duration(start: datetime, turbine_rows: Sequence[MeasurementRow]) -> timedelta:
    window_end = start + LOW_POWER_CAP
    known = [
        (r.timestamp, r.values[POWER])
        for r in turbine_rows
        if r.timestamp > start and r.values.get(POWER) is not None
    ]
    if not known:
        return timedelta(0)
    _, first_power = known[0]
    if first_power >= LOW_POWER_THRESHOLD_KW:
        return timedelta(0)
    for timestamp, power in known:
        if timestamp > window_end:
            break
        if power >= LOW_POWER_THRESHOLD_KW:
            return timestamp - start
    return LOW_POWER_CAP


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
