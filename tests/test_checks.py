from datetime import datetime, timedelta

from jev_turbine.checks import chattering, floods, long_stops
from jev_turbine.models import Event

T0 = datetime(2016, 1, 1, 12, 0, 0)


def ev(turbine="Kelmarsh 1", start=T0, status="Warning", message="msg", duration=None):
    return Event(
        turbine=turbine,
        start=start,
        end=None,
        duration_seconds=duration,
        status=status,
        code="1",
        message=message,
        iec_category=None,
    )


# --- chattering ---


def test_three_starts_within_ten_minutes_flags_all_three():
    events = [
        ev(start=T0),
        ev(start=T0 + timedelta(minutes=1)),
        ev(start=T0 + timedelta(minutes=9, seconds=58)),
    ]

    assert chattering(events) == {0, 1, 2}


def test_two_starts_within_ten_minutes_is_not_chattering():
    events = [ev(start=T0), ev(start=T0 + timedelta(minutes=5))]

    assert chattering(events) == set()


def test_exactly_ten_minutes_apart_is_outside_the_window():
    # Endpoints exactly 10 minutes apart: no anchor sees all three within the window.
    events = [ev(start=T0), ev(start=T0 + timedelta(minutes=5)), ev(start=T0 + timedelta(minutes=10))]

    assert chattering(events) == set()


def test_different_turbines_are_not_grouped_together():
    events = [
        ev(turbine="Kelmarsh 1", start=T0),
        ev(turbine="Kelmarsh 2", start=T0 + timedelta(minutes=1)),
        ev(turbine="Kelmarsh 3", start=T0 + timedelta(minutes=2)),
    ]

    assert chattering(events) == set()


def test_different_messages_are_not_grouped_together():
    events = [
        ev(message="a", start=T0),
        ev(message="b", start=T0 + timedelta(minutes=1)),
        ev(message="c", start=T0 + timedelta(minutes=2)),
    ]

    assert chattering(events) == set()


def test_input_need_not_be_pre_sorted_within_a_group():
    events = [
        ev(start=T0 + timedelta(minutes=9)),
        ev(start=T0),
        ev(start=T0 + timedelta(minutes=1)),
    ]

    assert chattering(events) == {0, 1, 2}


# --- floods ---


def _non_info_run(n, start=T0, step=timedelta(seconds=30)):
    return [ev(status="Warning", start=start + i * step, message=f"m{i}") for i in range(n)]


def test_ten_non_informational_events_is_not_a_flood():
    events = _non_info_run(10)

    assert floods(events) == set()


def test_eleven_non_informational_events_is_a_flood():
    events = _non_info_run(11)

    assert floods(events) == set(range(11))


def test_flood_ignores_informational_events():
    events = _non_info_run(10) + [ev(status="Informational", start=T0, message="System OK")]

    assert floods(events) == set()


def test_eleventh_event_pinned_exactly_ten_minutes_after_the_first_is_not_a_flood():
    # The window anchored at the first event only reaches starts strictly before
    # first + 10:00, so pinning the 11th event exactly at that mark keeps every
    # window at 10, not 11: not a flood.
    events = _non_info_run(10, start=T0, step=timedelta(seconds=1)) + [
        ev(status="Warning", start=T0 + timedelta(minutes=10), message="eleventh")
    ]

    assert floods(events) == set()


def test_flood_counts_across_turbines():
    events = [
        ev(turbine="Kelmarsh 1", status="Warning", start=T0 + i * timedelta(seconds=10), message=f"m{i}")
        if i % 2 == 0
        else ev(turbine="Kelmarsh 2", status="Stop", start=T0 + i * timedelta(seconds=10), message=f"m{i}")
        for i in range(11)
    ]

    assert floods(events) == set(range(11))


# --- long_stops ---


def test_stop_over_24_hours_is_flagged():
    events = [ev(status="Stop", duration=24 * 3600 + 1)]

    assert long_stops(events) == {0}


def test_stop_at_exactly_24_hours_is_not_flagged():
    events = [ev(status="Stop", duration=24 * 3600)]

    assert long_stops(events) == set()


def test_non_stop_status_is_never_a_long_stop():
    events = [ev(status="Warning", duration=48 * 3600)]

    assert long_stops(events) == set()


def test_stop_with_no_duration_is_not_flagged():
    events = [ev(status="Stop", duration=None)]

    assert long_stops(events) == set()
