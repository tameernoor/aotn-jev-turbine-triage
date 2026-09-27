from datetime import datetime, timedelta, timezone

from jev_turbine.context import build_context
from jev_turbine.measurements import MeasurementRow
from jev_turbine.models import Event

START = datetime(2016, 1, 24, 16, 51, 17, tzinfo=timezone.utc)


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


def _row(minutes_from_start: int, **values: float | None) -> MeasurementRow:
    """A 10-minute row whose END timestamp is `minutes_from_start` minutes away
    from START (negative = before). `values` gives only the columns that matter to
    the test; the rest default to None, as measurements.load_measurements would
    leave a column that never appeared in the source."""
    all_values = {
        "Power (kW)": None,
        "Wind speed (m/s)": None,
        "Rotor speed (RPM)": None,
        "Grid frequency (Hz)": None,
        "Grid voltage (V)": None,
    }
    all_values.update(values)
    return MeasurementRow(timestamp=START + timedelta(minutes=minutes_from_start), values=all_values)


def _lines(event: Event, rows: list[MeasurementRow], events: list[Event] | None = None) -> list[str]:
    return build_context(event, rows, events if events is not None else [event]).split("\n")


# --- no data ----------------------------------------------------------------------


def test_no_data_around_the_event_when_before_and_after_power_are_both_missing():
    event = _event()
    rows = [_row(-30, **{"Power (kW)": None}), _row(30, **{"Power (kW)": None})]

    lines = _lines(event, rows)

    assert lines[0] == "No 10-minute data around this event."
    # History lines still follow the "no data" line.
    assert lines[1] == "Same message on this turbine in the previous 7 days: 0 times."
    assert lines[2] == "Other turbines stopped in the same 10 minutes: no."


def test_no_data_around_the_event_when_there_are_no_rows_at_all():
    event = _event()

    lines = _lines(event, [])

    assert lines[0] == "No 10-minute data around this event."


# --- before / after / rounding -----------------------------------------------------


def test_before_and_after_lines_are_rounded_and_comma_formatted():
    event = _event()
    rows = [
        _row(-50, **{"Power (kW)": 1500.0, "Wind speed (m/s)": 9.76}),
        _row(-40, **{"Power (kW)": 1540.0, "Wind speed (m/s)": 9.84}),
        _row(10, **{"Power (kW)": 621.0}),
        _row(20, **{"Power (kW)": 623.0}),
    ]

    lines = _lines(event, rows)

    assert lines[0] == "Before the event: power 1,520 kW, wind 9.8 m/s (60-minute averages)."
    assert lines[1] == "After the event: power 620 kW; power stayed below 50 kW for 0 min."


def test_before_line_omits_wind_when_wind_is_missing():
    event = _event()
    rows = [_row(-10, **{"Power (kW)": 600.0}), _row(10, **{"Power (kW)": 600.0})]

    lines = _lines(event, rows)

    assert lines[0] == "Before the event: power 600 kW (60-minute averages)."


def test_after_line_is_omitted_when_only_before_power_is_present():
    event = _event()
    rows = [_row(-10, **{"Power (kW)": 600.0})]

    lines = _lines(event, rows)

    assert lines[0] == "Before the event: power 600 kW (60-minute averages)."
    assert not any(line.startswith("After the event") for line in lines)


# --- drop and recovery --------------------------------------------------------------


def test_power_never_drops_below_50kw_gives_zero_duration():
    event = _event()
    rows = [
        _row(-10, **{"Power (kW)": 600.0}),
        _row(10, **{"Power (kW)": 580.0}),
        _row(20, **{"Power (kW)": 590.0}),
    ]

    lines = _lines(event, rows)

    assert lines[1] == "After the event: power 580 kW; power stayed below 50 kW for 0 min."


def test_drop_and_recovery_duration_rounds_to_nearest_ten_minutes():
    event = _event()
    rows = [
        _row(-10, **{"Power (kW)": 600.0}),
        _row(10, **{"Power (kW)": 0.0}),
        _row(370, **{"Power (kW)": 60.0}),  # recovers 6h10min after the start
    ]

    lines = _lines(event, rows)

    assert "power stayed below 50 kW for 6 h 10 min." in lines[1]


def test_drop_duration_is_capped_at_24_hours_when_never_recovered():
    event = _event()
    rows = [
        _row(-10, **{"Power (kW)": 600.0}),
        _row(10, **{"Power (kW)": 0.0}),
        # Still below 50 kW at 2 days out; never recovers within the data.
        _row(60 * 48, **{"Power (kW)": 10.0}),
    ]

    lines = _lines(event, rows)

    assert "power stayed below 50 kW for 24 h." in lines[1]


def test_drop_duration_skips_missing_readings_when_looking_for_recovery():
    event = _event()
    rows = [
        _row(-10, **{"Power (kW)": 600.0}),
        _row(10, **{"Power (kW)": 0.0}),
        _row(20, **{"Power (kW)": None}),  # a gap in the middle of the drop
        _row(30, **{"Power (kW)": 100.0}),  # recovery, 30 min after the start
    ]

    lines = _lines(event, rows)

    assert "power stayed below 50 kW for 30 min." in lines[1]


# --- rotor and grid -----------------------------------------------------------------


def test_rotor_and_grid_lines_are_omitted_when_no_values_exist():
    event = _event()
    rows = [_row(-10, **{"Power (kW)": 600.0}), _row(10, **{"Power (kW)": 600.0})]

    lines = _lines(event, rows)

    assert not any(line.startswith("Rotor") for line in lines)
    assert not any(line.startswith("Grid") for line in lines)


def test_rotor_line_rounds_to_the_nearest_whole_rpm():
    event = _event()
    rows = [
        _row(-10, **{"Power (kW)": 600.0}),
        _row(10, **{"Power (kW)": 0.0, "Rotor speed (RPM)": 0.4}),
        _row(20, **{"Power (kW)": 0.0, "Rotor speed (RPM)": 0.2}),
    ]

    lines = _lines(event, rows)

    assert "Rotor after the event: 0 RPM." in lines


def test_grid_line_reports_min_and_max_rounded_and_can_omit_one_side():
    event = _event()
    rows = [
        _row(-10, **{"Power (kW)": 600.0}),
        _row(10, **{"Power (kW)": 600.0, "Grid frequency (Hz)": 49.978}),
        _row(20, **{"Power (kW)": 600.0, "Grid frequency (Hz)": 50.021}),
    ]

    lines = _lines(event, rows)

    assert "Grid in the hour around the event: frequency 49.98 to 50.02 Hz." in lines


def test_grid_line_uses_a_thirty_minute_half_window_around_the_start():
    event = _event()
    rows = [
        _row(-10, **{"Power (kW)": 600.0}),
        _row(10, **{"Power (kW)": 600.0}),
        # Just outside the +/- 30 minute grid window; must not affect min/max.
        _row(45, **{"Power (kW)": 600.0, "Grid voltage (V)": 999.0}),
        _row(20, **{"Power (kW)": 600.0, "Grid voltage (V)": 690.0}),
    ]

    lines = _lines(event, rows)

    assert "voltage 690 to 690 V" in "\n".join(lines)


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


# --- full render, shaped like the plan's worked example -------------------------------


def test_full_render_matches_the_documented_example_shape():
    event = _event()
    rows = [
        _row(-50, **{"Power (kW)": 1500.0, "Wind speed (m/s)": 9.76}),
        _row(-40, **{"Power (kW)": 1540.0, "Wind speed (m/s)": 9.84}),
        _row(10, **{"Power (kW)": 0.0, "Rotor speed (RPM)": 0.0, "Grid frequency (Hz)": 49.978, "Grid voltage (V)": 690.0}),
        _row(370, **{"Power (kW)": 60.0}),  # recovers 6h10min after the start
    ]
    events = [
        _event(start=event.start - timedelta(days=2), message="Frequency converter error"),
        _event(start=event.start - timedelta(minutes=5), status="Warning", message="Grid loss"),
        _event(turbine="Kelmarsh 6", start=event.start, status="Stop"),
        event,
    ]

    text = build_context(event, rows, events)

    assert text == "\n".join(
        [
            "Before the event: power 1,520 kW, wind 9.8 m/s (60-minute averages).",
            "After the event: power 0 kW; power stayed below 50 kW for 6 h 10 min.",
            "Rotor after the event: 0 RPM.",
            "Grid in the hour around the event: frequency 49.98 to 49.98 Hz, voltage 690 to 690 V.",
            "Same message on this turbine in the previous 7 days: 1 times.",
            "Event just before on this turbine (within 30 min): Grid loss.",
            "Other turbines stopped in the same 10 minutes: yes (1).",
        ]
    )
