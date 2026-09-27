from datetime import datetime, timedelta, timezone

from jev_turbine.context import build_context
from jev_turbine.measurements import connect_in_memory
from jev_turbine.models import Event

# Exactly on the 10-minute grid, so "before" and "after" line up with simple
# offsets from START: before is offsets -60..-10, after is offsets 0..50.
START = datetime(2016, 1, 24, 17, 0, 0, tzinfo=timezone.utc)

# The real Kelmarsh 1 example: off the grid (17 seconds past the minute), so the
# row covering it (stamped 16:50:00) straddles the start.
OFFGRID_START = datetime(2016, 1, 24, 16, 51, 17, tzinfo=timezone.utc)


def _event(
    turbine: str = "Kelmarsh 1",
    start: datetime = START,
    status: str = "Stop",
    message: str = "Frequency converter error",
) -> Event:
    return Event(
        turbine=turbine,
        start=start,
        end=None,
        duration_seconds=None,
        status=status,
        code="3110",
        message=message,
        iec_category=None,
    )


def _row(ts: datetime, power=None, wind=None, rotor=None, freq=None, volt=None, turbine: int = 1) -> tuple:
    return (turbine, ts, power, wind, rotor, freq, volt)


def _off(minutes: int, power=None, wind=None, rotor=None, freq=None, volt=None, turbine: int = 1) -> tuple:
    """A row `minutes` from START (may be negative), per the (corrected)
    convention that ts marks the START of its own 10-minute period."""
    return _row(START + timedelta(minutes=minutes), power, wind, rotor, freq, volt, turbine)


def _db(rows: list[tuple]):
    con = connect_in_memory()
    if rows:
        con.executemany("INSERT INTO measurements VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return con


def _lines(event: Event, rows: list[tuple], events: list[Event] | None = None) -> list[str]:
    con = _db(rows)
    return build_context(event, con, events if events is not None else [event]).split("\n")


# --- no data ----------------------------------------------------------------------


def test_no_data_around_the_event_when_before_and_after_power_are_both_missing():
    event = _event()
    rows = [_off(-20), _off(20)]

    lines = _lines(event, rows)

    assert lines[0] == "No 10-minute data around this event."
    # History lines still follow the "no data" line.
    assert lines[1] == "Same message on this turbine in the previous 7 days: 0 times."
    assert lines[2] == "Other turbines stopped in the same 10 minutes: no."


def test_no_data_around_the_event_when_there_are_no_rows_at_all():
    event = _event()

    lines = _lines(event, [])

    assert lines[0] == "No 10-minute data around this event."


# --- window boundaries: grid-aligned start and a straddling row -------------------


def test_a_row_exactly_at_the_start_counts_as_after_not_straddling():
    event = _event()
    # ts == start: interval [start, start+10min) does not dip before the start, so
    # it is the first of the 6 after rows, not a straddler.
    rows = [_off(0, power=50.0)]

    lines = _lines(event, rows)

    assert lines[0] == "After the event: power 50 kW; power stayed below 50 kW for 0 min."


def test_a_row_ending_exactly_at_the_start_counts_as_before_not_straddling():
    event = _event()
    # ts + 10min == start: interval [ts, start) does not reach past the start, so
    # it is the last of the 6 before rows, not a straddler.
    rows = [_off(-10, power=600.0)]

    lines = _lines(event, rows)

    assert lines[0] == "Before the event: power 600 kW (60-minute averages)."


def test_row_straddling_an_off_grid_start_counts_toward_neither_mean():
    event = _event(start=OFFGRID_START)
    grid = OFFGRID_START.replace(minute=50, second=0, microsecond=0)  # 16:50:00
    rows = [
        # Fully before: ends exactly at the straddling row's own start.
        _row(grid - timedelta(minutes=10), power=600.0),
        # Straddles OFFGRID_START (16:51:17): starts at 16:50:00, ends 17:00:00.
        # If this leaked into either mean it would show up as 999 kW.
        _row(grid, power=999.0),
        # Fully after: starts exactly at the straddling row's own end.
        _row(grid + timedelta(minutes=10), power=0.0),
    ]

    lines = _lines(event, rows)

    assert lines[0] == "Before the event: power 600 kW (60-minute averages)."
    assert "power 0 kW" in lines[1]
    assert not any("999" in line for line in lines)


# --- before / after / rounding -----------------------------------------------------


def test_before_and_after_lines_are_rounded_and_comma_formatted():
    event = _event()
    rows = [
        _off(-50, power=1500.0, wind=9.76),
        _off(-40, power=1540.0, wind=9.84),
        _off(10, power=621.0),
        _off(20, power=623.0),
    ]

    lines = _lines(event, rows)

    assert lines[0] == "Before the event: power 1,520 kW, wind 9.8 m/s (60-minute averages)."
    assert lines[1] == "After the event: power 620 kW; power stayed below 50 kW for 0 min."


def test_before_line_omits_wind_when_wind_is_missing():
    event = _event()
    rows = [_off(-10, power=600.0), _off(10, power=600.0)]

    lines = _lines(event, rows)

    assert lines[0] == "Before the event: power 600 kW (60-minute averages)."


def test_after_line_is_omitted_when_only_before_power_is_present():
    event = _event()
    rows = [_off(-10, power=600.0)]

    lines = _lines(event, rows)

    assert lines[0] == "Before the event: power 600 kW (60-minute averages)."
    assert not any(line.startswith("After the event") for line in lines)


# --- drop and recovery --------------------------------------------------------------


def test_power_never_drops_below_50kw_gives_zero_duration():
    event = _event()
    rows = [_off(-10, power=600.0), _off(10, power=580.0), _off(20, power=590.0)]

    lines = _lines(event, rows)

    assert lines[1] == "After the event: power 580 kW; power stayed below 50 kW for 0 min."


def test_drop_and_recovery_duration_is_exact_not_a_floor():
    event = _event()
    rows = [_off(-10, power=600.0)]
    # Known, low readings every 20 minutes (no gap) from the start out to the
    # recovery, so this is a genuine recovery, not a gap.
    rows += [_off(m, power=0.0) for m in range(10, 370, 20)]
    rows += [_off(370, power=60.0)]  # recovers 6h10min after the start

    lines = _lines(event, rows)

    assert "power stayed below 50 kW for 6 h 10 min." in lines[1]
    assert "at least" not in lines[1]


def test_drop_duration_reaches_24_hours_with_no_gap_and_no_recovery():
    event = _event()
    rows = [_off(-10, power=600.0)]
    # Known, low readings every 20 minutes (well under the 30-minute gap
    # threshold) from just after the start out to just short of 24 hours: no gap,
    # no recovery, so the duration is reported as the 24-hour cap.
    rows += [_off(m, power=10.0) for m in range(10, 1440, 20)]

    lines = _lines(event, rows)

    assert "power stayed below 50 kW for at least 24 h." in lines[1]


def test_drop_duration_stops_at_a_gap_of_more_than_30_minutes():
    event = _event()
    rows = [_off(-10, power=600.0), _off(10, power=0.0)]
    # No further reading at all: the data just stops after this one.

    lines = _lines(event, rows)

    assert "power stayed below 50 kW for at least 20 min, then no power data." in lines[1]


def test_drop_duration_gap_detection_stops_at_the_first_gap_even_if_more_low_data_exists_later():
    event = _event()
    rows = [
        _off(-10, power=600.0),
        _off(10, power=0.0),
        # Still below 50 kW at 2 days out, but the gap since the previous known
        # reading (way more than 30 minutes) is what matters, not this value.
        _off(60 * 48, power=10.0),
    ]

    lines = _lines(event, rows)

    assert "power stayed below 50 kW for at least 20 min, then no power data." in lines[1]


def test_drop_duration_skips_a_short_gap_when_looking_for_recovery():
    event = _event()
    rows = [
        _off(-10, power=600.0),
        _off(10, power=0.0),
        _off(20),  # a gap in the readings, but only 10 minutes: not "the data stopping"
        _off(30, power=100.0),  # recovery, 30 min after the start
    ]

    lines = _lines(event, rows)

    assert "power stayed below 50 kW for 30 min." in lines[1]


def test_the_interval_to_the_first_known_reading_is_not_itself_a_gap():
    """A first known reading arriving more than 30 minutes after the start must
    not be mistaken for "the data stopping" at 0 minutes in: the start-to-first-
    reading stretch is not a gap, however late that reading is."""
    event = _event()
    rows = [_off(40, power=10.0)]  # first known reading, 40 min after the start: < 50 kW
    # No gap after it either: recovers cleanly 10 minutes later.
    rows += [_off(50, power=100.0)]

    lines = _lines(event, rows)

    assert "power stayed below 50 kW for 50 min." in lines[0]
    assert "at least" not in lines[0]
    assert "no power data" not in lines[0]


def test_a_reading_after_a_gap_is_never_a_recovery_even_if_its_own_power_is_high():
    """A gap of more than 30 minutes resolves the search first: the reading that
    ends the silence is never treated as a recovery (or "did not drop"), no
    matter how high its own power is."""
    event = _event()
    rows = [
        _off(0, power=0.0),  # first known reading: confirms the drop
        # Nothing for 6 hours, then a healthy reading: still "then no power
        # data", not a recovery at +6h.
        _off(360, power=900.0),
    ]

    lines = _lines(event, rows)

    assert "power stayed below 50 kW for at least 10 min, then no power data." in lines[0]
    assert "900" not in lines[0]


# --- rotor and grid -----------------------------------------------------------------


def test_rotor_and_grid_lines_are_omitted_when_no_values_exist():
    event = _event()
    rows = [_off(-10, power=600.0), _off(10, power=600.0)]

    lines = _lines(event, rows)

    assert not any(line.startswith("Rotor") for line in lines)
    assert not any(line.startswith("Grid") for line in lines)


def test_rotor_line_rounds_to_the_nearest_whole_rpm():
    event = _event()
    rows = [_off(-10, power=600.0), _off(10, power=0.0, rotor=0.4), _off(20, power=0.0, rotor=0.2)]

    lines = _lines(event, rows)

    assert "Rotor after the event: 0 RPM." in lines


def test_grid_line_reports_min_and_max_rounded_and_can_omit_one_side():
    event = _event()
    rows = [
        _off(-10, power=600.0),
        _off(10, power=600.0, freq=49.978),
        _off(20, power=600.0, freq=50.021),
    ]

    lines = _lines(event, rows)

    assert "Grid in the hour around the event: frequency 49.98 to 50.02 Hz." in lines


def test_grid_line_is_the_straddling_row_plus_three_either_side():
    event = _event()
    rows = [
        _off(-10, power=600.0),
        _off(10, power=600.0),
        _off(30, volt=690.0),  # the 3rd row after the start row: still in range
        # Outside the window (more than 3 rows past the start row): must not
        # affect min/max, even though it is well within 60 minutes of the start.
        _off(40, volt=999.0),
    ]

    lines = _lines(event, rows)

    assert "voltage 690 to 690 V" in "\n".join(lines)
    assert not any("999" in line for line in lines)


# --- history: previous 7 days --------------------------------------------------------


def test_same_message_count_counts_only_strictly_earlier_starts_within_7_days():
    event = _event()
    events = [
        _event(start=event.start - timedelta(days=1)),
        _event(start=event.start - timedelta(days=6, hours=23)),
        _event(start=event.start - timedelta(days=7, hours=1)),  # just outside the window
        _event(start=event.start, message="a different message"),
        event,
    ]

    lines = _lines(event, [], events)

    assert "Same message on this turbine in the previous 7 days: 2 times." in lines


def test_same_message_count_ignores_other_turbines_and_other_messages():
    event = _event()
    events = [
        _event(turbine="Kelmarsh 2", start=event.start - timedelta(hours=1)),
        _event(start=event.start - timedelta(hours=1), message="Something else"),
        event,
    ]

    lines = _lines(event, [], events)

    assert "Same message on this turbine in the previous 7 days: 0 times." in lines


# --- history: event just before -------------------------------------------------------


def test_event_just_before_picks_the_nearest_non_informational_event_within_30_minutes():
    event = _event()
    events = [
        _event(start=event.start - timedelta(minutes=25), status="Informational", message="System OK"),
        _event(start=event.start - timedelta(minutes=20), status="Warning", message="Grid loss"),
        _event(start=event.start - timedelta(minutes=5), status="Stop", message="Nearest one"),
        event,
    ]

    lines = _lines(event, [], events)

    assert "Event just before on this turbine (within 30 min): Nearest one." in lines


def test_event_just_before_is_omitted_when_none_qualifies():
    event = _event()
    events = [
        _event(start=event.start - timedelta(minutes=45), status="Stop", message="Too far back"),
        _event(start=event.start - timedelta(minutes=10), status="Informational", message="Informational only"),
        event,
    ]

    lines = _lines(event, [], events)

    assert not any(line.startswith("Event just before") for line in lines)


# --- history: farm-wide stop -----------------------------------------------------------


def test_farm_wide_stop_counts_other_turbines_within_ten_minutes_either_side():
    event = _event()
    events = [
        _event(turbine="Kelmarsh 2", start=event.start + timedelta(minutes=9), status="Stop"),
        _event(turbine="Kelmarsh 3", start=event.start - timedelta(minutes=9), status="Stop"),
        _event(turbine="Kelmarsh 4", start=event.start + timedelta(minutes=11), status="Stop"),  # outside window
        _event(turbine="Kelmarsh 5", start=event.start, status="Warning"),  # not a Stop
        event,
    ]

    lines = _lines(event, [], events)

    assert "Other turbines stopped in the same 10 minutes: yes (2)." in lines


def test_farm_wide_stop_counts_distinct_turbines_not_events():
    event = _event()
    events = [
        _event(turbine="Kelmarsh 2", start=event.start + timedelta(minutes=2), status="Stop", message="A"),
        _event(turbine="Kelmarsh 2", start=event.start + timedelta(minutes=6), status="Stop", message="B"),
        event,
    ]

    lines = _lines(event, [], events)

    # Two qualifying Stop events, but both on the same other turbine: still "1".
    assert "Other turbines stopped in the same 10 minutes: yes (1)." in lines


def test_farm_wide_stop_ignores_a_stop_on_the_same_turbine():
    event = _event()
    events = [event]

    lines = _lines(event, [], events)

    assert "Other turbines stopped in the same 10 minutes: no." in lines


def test_farm_wide_stop_says_no_when_none_found():
    event = _event()
    events = [_event(turbine="Kelmarsh 2", start=event.start, status="Warning"), event]

    lines = _lines(event, [], events)

    assert "Other turbines stopped in the same 10 minutes: no." in lines


# --- full render, shaped like the README's example -------------------------------


def test_full_render_matches_the_documented_example_shape():
    event = _event()
    rows = [
        _off(-50, power=1500.0, wind=9.76),
        _off(-40, power=1540.0, wind=9.84),
        _off(10, power=0.0, rotor=0.0, freq=49.978, volt=690.0),
    ]
    # Known, low readings every 20 minutes (no gap) out to the recovery.
    rows += [_off(m, power=0.0) for m in range(30, 370, 20)]
    rows += [_off(370, power=60.0)]  # recovers 6h10min after the start
    events = [
        _event(start=event.start - timedelta(days=2), message="Frequency converter error"),
        _event(start=event.start - timedelta(minutes=5), status="Warning", message="Grid loss"),
        _event(turbine="Kelmarsh 6", start=event.start, status="Stop"),
        event,
    ]

    lines = _lines(event, rows, events)

    assert lines == [
        "Before the event: power 1,520 kW, wind 9.8 m/s (60-minute averages).",
        "After the event: power 0 kW; power stayed below 50 kW for 6 h 10 min.",
        "Rotor after the event: 0 RPM.",
        "Grid in the hour around the event: frequency 49.98 to 49.98 Hz, voltage 690 to 690 V.",
        "Same message on this turbine in the previous 7 days: 1 times.",
        "Event just before on this turbine (within 30 min): Grid loss.",
        "Other turbines stopped in the same 10 minutes: yes (1).",
    ]
