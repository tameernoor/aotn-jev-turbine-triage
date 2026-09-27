import asyncio
from datetime import datetime, timedelta, timezone

from fakes import FakeJev, answers

from jev_turbine.escalate import escalate
from jev_turbine.measurements import connect_in_memory
from jev_turbine.models import Event
from jev_turbine.triage import ACT_NOW, MONITOR, NO_ACTION, RUNNING, UNCLEAR, triage

# Grid-aligned, so an after-window row at offset 0 starts exactly at the event start:
# no straddling row, so a single row is both "the after mean" and "the first known
# reading from the event start", the simplest fixture for the production-number rules.
T0 = datetime(2016, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

# Step-1 judgments (fakes.answers shape), in questions/event.yaml's five ids, pre-seeded
# straight into the step-1 cache, so triage() never needs an actual Jev call and each
# test controls exactly which of its events step 1 leaves uncertain. Step 1's own rules
# live in triage.py (Task 1); this file only exercises step 2 (escalate.py, now a
# code-only read of production numbers, no Jev call at all).

# cause unclear via an uncertain cause-chain read (names_routine): the "in practice"
# case decision 1 describes, and the one most of these tests use.
CAUSE_UNCLEAR_VIA_UNCERTAIN_ROUTINE = answers(names_safety_hazard=0.1, names_physical_damage=0.1, names_routine=0.5)
# cause unclear via three confident no's (status not Warning): nothing read was itself
# uncertain, so step 1's reason is a plain "cause unclear", not "uncertain: ...".
CAUSE_UNCLEAR_VIA_CONFIDENT_NOS = answers(
    names_safety_hazard=0.1,
    names_physical_damage=0.1,
    names_routine=0.1,
    names_outside_condition=0.1,
    names_turbine_problem=0.1,
)
# safety_hazard itself uncertain, alongside an uncertain cause-chain read: step 1's
# "uncertain: ..." reason names names_safety_hazard, so step 2 must not touch it.
SAFETY_UNCERTAIN_CAUSE_UNCLEAR = answers(names_safety_hazard=0.5, names_physical_damage=0.1, names_routine=0.5)
# physical_damage itself uncertain, same idea.
DAMAGE_UNCERTAIN_CAUSE_UNCLEAR = answers(names_safety_hazard=0.1, names_physical_damage=0.5, names_routine=0.5)
PLANNED = answers(names_safety_hazard=0.1, names_physical_damage=0.1, names_routine=0.9)
RUNNING_WHILE_WARNING = answers(
    names_safety_hazard=0.1,
    names_physical_damage=0.1,
    names_routine=0.1,
    names_outside_condition=0.1,
    names_turbine_problem=0.1,
)


def ev(turbine="Kelmarsh 1", start=T0, end=None, duration=None, status="Stop", code="1", message="msg", iec_category=None):
    return Event(
        turbine=turbine,
        start=start,
        end=end,
        duration_seconds=duration,
        status=status,
        code=code,
        message=message,
        iec_category=iec_category,
    )


def step1(events, cache):
    return asyncio.run(triage(events, FakeJev().ask, cache))


def db(rows: list[tuple]):
    con = connect_in_memory()
    if rows:
        con.executemany("INSERT INTO measurements VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
    return con


def row(minutes: int, power: float | None, turbine: int = 1) -> tuple:
    return (turbine, T0 + timedelta(minutes=minutes), power, None, None, None, None)


# --- selection: only step-1-monitor-with-unclear-cause events escalate --------------


def test_only_cause_unclear_events_escalate():
    unclear_event = ev(status="Stop", message="Pitch fault", start=T0)
    planned_event = ev(status="Stop", message="Cable unwind", start=T0 + timedelta(hours=2))
    running_while_warning = ev(status="Warning", message="Warm-up", start=T0 + timedelta(hours=4))
    events = [unclear_event, planned_event, running_while_warning]
    cache = {
        "Stop": {"Pitch fault": CAUSE_UNCLEAR_VIA_UNCERTAIN_ROUTINE, "Cable unwind": PLANNED},
        "Warning": {"Warm-up": RUNNING_WHILE_WARNING},
    }
    results = step1(events, cache)

    # Sanity: confirm each scenario landed where the test intends before escalating.
    assert results[0].triage == MONITOR and results[0].cause == UNCLEAR
    assert results[1].triage == NO_ACTION and results[1].cause == "planned"
    assert results[2].triage == MONITOR and results[2].cause == RUNNING

    con = db([row(0, 100.0)])
    final = escalate(results, events, events, con)

    assert final[0].triage == NO_ACTION and final[0].reasons == ["kept producing"]
    assert final[0].step1_triage == MONITOR
    # Neither the planned nor the running-while-warning event was touched: no
    # production numbers ever looked at (context/step1_triage stay None).
    assert final[1] == results[1]
    assert final[2] == results[2]


def test_cause_unclear_via_confident_nos_also_escalates():
    event = ev(status="Stop", message="No speed development")
    cache = {"Stop": {"No speed development": CAUSE_UNCLEAR_VIA_CONFIDENT_NOS}}
    results = step1([event], cache)
    assert results[0].reasons == ["cause unclear"]

    con = db([row(0, 100.0)])
    final = escalate(results, [event], [event], con)

    assert final[0].triage == NO_ACTION
    assert final[0].reasons == ["kept producing"]


# --- safety/damage uncertainty is never touched by step 2 ---------------------------


def test_safety_hazard_uncertainty_stays_monitor_untouched():
    event = ev(status="Stop", message="Ambiguous")
    cache = {"Stop": {"Ambiguous": SAFETY_UNCERTAIN_CAUSE_UNCLEAR}}
    results = step1([event], cache)
    assert results[0].triage == MONITOR and results[0].cause == UNCLEAR
    assert results[0].reasons == ["uncertain: names_safety_hazard, names_routine"]

    con = db([row(0, 100.0)])  # would decide "kept producing" if this event were selected
    final = escalate(results, [event], [event], con)

    assert final[0] == results[0]
    assert final[0].step1_triage is None
    assert final[0].context is None


def test_physical_damage_uncertainty_stays_monitor_untouched():
    event = ev(status="Stop", message="Ambiguous")
    cache = {"Stop": {"Ambiguous": DAMAGE_UNCERTAIN_CAUSE_UNCLEAR}}
    results = step1([event], cache)
    assert results[0].triage == MONITOR and results[0].cause == UNCLEAR
    assert results[0].reasons == ["uncertain: names_physical_damage, names_routine"]

    con = db([row(0, 100.0)])
    final = escalate(results, [event], [event], con)

    assert final[0] == results[0]


# --- kept producing -------------------------------------------------------------------


def test_kept_producing_sets_running_no_action():
    event = ev(status="Stop", message="Pitch fault")
    cache = {"Stop": {"Pitch fault": CAUSE_UNCLEAR_VIA_UNCERTAIN_ROUTINE}}
    results = step1([event], cache)

    con = db([row(0, 100.0)])  # after_power 100 >= 50, first known reading, recovered 0
    final = escalate(results, [event], [event], con)

    assert final[0].triage == NO_ACTION
    assert final[0].reasons == ["kept producing"]
    assert final[0].cause == RUNNING


def test_kept_producing_requires_power_never_dropped_below_50():
    # after_power averages to >= 50, but the turbine's power did drop below 50 kW at
    # some point after the event before recovering: low_power_recovered_seconds > 0,
    # so this is not "kept producing".
    event = ev(status="Stop", message="Pitch fault")
    cache = {"Stop": {"Pitch fault": CAUSE_UNCLEAR_VIA_UNCERTAIN_ROUTINE}}
    results = step1([event], cache)

    con = db([row(0, 10.0), row(10, 100.0)])  # dropped first, recovered 10 minutes later
    final = escalate(results, [event], [event], con)

    # after_power (mean of 10.0 and 100.0) is >= 50, but recovered_seconds is 600, not
    # 0, so the "stopped" rule (after_power < 50) doesn't apply either: unchanged.
    assert final[0].triage == results[0].triage
    assert final[0].reasons == results[0].reasons
    assert final[0].cause == results[0].cause
    assert final[0].step1_triage == MONITOR  # still looked at
    assert final[0].context is not None


# --- stopped ---------------------------------------------------------------------------


def test_stopped_stays_monitor_with_the_stopped_reason():
    event = ev(status="Stop", message="Pitch fault")
    cache = {"Stop": {"Pitch fault": CAUSE_UNCLEAR_VIA_UNCERTAIN_ROUTINE}}
    results = step1([event], cache)

    con = db([row(0, 10.0)])  # after_power 10 < 50
    final = escalate(results, [event], [event], con)

    assert final[0].triage == MONITOR
    assert final[0].reasons == ["stopped, cause unclear"]
    assert final[0].cause == UNCLEAR


# --- no data: stays exactly as step 1 had it, but was still looked at ----------------


def test_no_data_leaves_the_result_unchanged_but_marks_it_as_looked_at():
    event = ev(status="Stop", message="Pitch fault")
    cache = {"Stop": {"Pitch fault": CAUSE_UNCLEAR_VIA_UNCERTAIN_ROUTINE}}
    results = step1([event], cache)

    con = db([])  # no measurements at all
    final = escalate(results, [event], [event], con)

    assert final[0].triage == results[0].triage
    assert final[0].reasons == results[0].reasons
    assert final[0].cause == results[0].cause
    assert final[0].step1_triage == MONITOR
    assert final[0].step1_reasons == ["uncertain: names_routine"]
    assert final[0].context is not None
    assert "No 10-minute data" in final[0].context


# --- step-1 fields are kept alongside the step-2 result -------------------------------


def test_step1_fields_are_kept():
    event = ev(status="Stop", message="Pitch fault")
    cache = {"Stop": {"Pitch fault": CAUSE_UNCLEAR_VIA_UNCERTAIN_ROUTINE}}
    results = step1([event], cache)

    con = db([row(0, 100.0)])
    final = escalate(results, [event], [event], con)

    assert final[0].step1_triage == MONITOR
    assert final[0].step1_reasons == ["uncertain: names_routine"]
    assert final[0].turbine == event.turbine
    assert final[0].start == event.start
    assert final[0].status == event.status
    assert final[0].message == event.message
    assert final[0].context is not None
    assert "power" in final[0].context.lower()


# --- code-check reasons from step 1 survive onto the step-2 result -------------------


def test_long_stop_reason_and_floor_are_reapplied_to_a_kept_producing_result():
    event = ev(status="Stop", message="Pitch fault", duration=30 * 3600)
    cache = {"Stop": {"Pitch fault": CAUSE_UNCLEAR_VIA_UNCERTAIN_ROUTINE}}
    results = step1([event], cache)
    assert results[0].reasons == ["uncertain: names_routine", "long stop"]

    con = db([row(0, 100.0)])  # would be no_action ("kept producing") on its own
    final = escalate(results, [event], [event], con)

    # no_action floors to monitor on account of the long stop, same as step 1.
    assert final[0].triage == MONITOR
    assert final[0].reasons == ["kept producing", "long stop"]


def test_chattering_reason_is_reapplied_to_a_stopped_result():
    # Three identical alarms at the same instant: still 3 within the 10-minute
    # chattering window, and all share the same grid-aligned start, so the same
    # single measurement row resolves "stopped" for all three.
    events = [ev(status="Stop", message="Yaw error", start=T0) for _ in range(3)]
    cache = {"Stop": {"Yaw error": CAUSE_UNCLEAR_VIA_UNCERTAIN_ROUTINE}}
    results = step1(events, cache)
    assert all(r.chattering for r in results)
    assert all(r.reasons == ["uncertain: names_routine", "chattering"] for r in results)

    con = db([row(0, 10.0)])  # after_power 10 < 50 for every event, they all share T0
    final = escalate(results, events, events, con)

    for r in final:
        assert r.triage == MONITOR
        assert r.reasons == ["stopped, cause unclear", "chattering"]


def test_flood_flag_is_carried_over_to_the_step2_result():
    events = [ev(status="Warning", message=f"m{i}", start=T0 + timedelta(seconds=i)) for i in range(11)]
    cache = {"Warning": {f"m{i}": PLANNED for i in range(11)}}
    cache["Warning"]["m0"] = CAUSE_UNCLEAR_VIA_UNCERTAIN_ROUTINE
    results = step1(events, cache)
    assert results[0].flood is True
    assert results[0].triage == MONITOR and results[0].cause == UNCLEAR

    con = db([row(0, 100.0)])
    final = escalate(results, events, events, con)

    assert final[0].flood is True
    assert final[0].triage == NO_ACTION
    assert final[0].reasons == ["kept producing"]


# --- act_now/informational events, and events step 1 already resolved, are untouched --


def test_act_now_and_informational_events_are_never_selected():
    act_now_event = ev(status="Stop", message="Emergency stop", start=T0)
    informational_event = ev(status="Informational", message="System OK", start=T0 + timedelta(hours=1))
    events = [act_now_event, informational_event]
    safety_act_now = answers(
        names_safety_hazard=0.9,
        names_physical_damage=0.1,
        names_routine=0.1,
        names_outside_condition=0.1,
        names_turbine_problem=0.1,
    )
    cache = {"Stop": {"Emergency stop": safety_act_now}}
    results = step1(events, cache)
    assert results[0].triage == ACT_NOW
    assert results[1].triage == NO_ACTION and results[1].cause is None

    con = db([row(0, 100.0)])
    final = escalate(results, events, events, con)

    assert final == results


# --- empty selection: no query at all, results returned unchanged --------------------


def test_nothing_to_escalate_returns_results_unchanged_without_opening_the_db():
    event = ev(status="Stop", message="Cable unwind")
    cache = {"Stop": {"Cable unwind": PLANNED}}
    results = step1([event], cache)

    con = db([])
    final = escalate(results, [event], [event], con)

    assert final == results
