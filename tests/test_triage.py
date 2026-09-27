import asyncio
from datetime import datetime, timedelta, timezone

from fakes import FakeJev, answers

from jev_turbine.judgments import Judgments
from jev_turbine.models import Event
from jev_turbine.triage import (
    ACT_NOW,
    EXTERNAL,
    FAULT,
    MONITOR,
    NO_ACTION,
    PLANNED,
    RUNNING,
    UNCLEAR,
    apply_step1_rules,
    derive_cause,
    triage,
)

T0 = datetime(2016, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def ev(turbine="Kelmarsh 1", start=T0, end=None, duration=None, status="Warning", code="1", message="msg", iec_category=None):
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


def run(events, fake, cache=None):
    return asyncio.run(triage(events, fake.ask, cache))


# A confident "yes" and a confident "no" for a noul, matching judgments.py's YES/NO
# thresholds (0.8 / 0.2 inclusive).
YES = 0.9
NO = 0.1
BETWEEN = 0.5  # uncertain: strictly between NO and YES


# --- derive_cause(): each path, one question at a time -----------------------------


def test_derive_cause_routine_yes_is_planned():
    j = Judgments(answers(names_routine=YES))
    assert derive_cause(j, status="Stop") == PLANNED


def test_derive_cause_outside_condition_yes_is_external():
    j = Judgments(answers(names_routine=NO, names_outside_condition=YES))
    assert derive_cause(j, status="Stop") == EXTERNAL


def test_derive_cause_turbine_problem_yes_is_fault():
    j = Judgments(answers(names_routine=NO, names_outside_condition=NO, names_turbine_problem=YES))
    assert derive_cause(j, status="Stop") == FAULT


def test_derive_cause_all_no_and_warning_is_running():
    j = Judgments(answers(names_routine=NO, names_outside_condition=NO, names_turbine_problem=NO))
    assert derive_cause(j, status="Warning") == RUNNING


def test_derive_cause_all_no_and_not_warning_is_unclear():
    j = Judgments(answers(names_routine=NO, names_outside_condition=NO, names_turbine_problem=NO))
    assert derive_cause(j, status="Stop") == UNCLEAR


# --- derive_cause(): an uncertain read stops the chain, with the right id ----------


def test_derive_cause_uncertain_routine_is_unclear_and_stops_the_chain():
    j = Judgments(answers(names_routine=BETWEEN, names_outside_condition=YES, names_turbine_problem=YES))
    assert derive_cause(j, status="Stop") == UNCLEAR
    assert j.uncertain == ["names_routine"]
    assert j.read == ["names_routine"]  # the later two are never read


def test_derive_cause_uncertain_outside_condition_is_unclear_and_stops_the_chain():
    j = Judgments(answers(names_routine=NO, names_outside_condition=BETWEEN, names_turbine_problem=YES))
    assert derive_cause(j, status="Stop") == UNCLEAR
    assert j.uncertain == ["names_outside_condition"]
    assert "names_turbine_problem" not in j.read


def test_derive_cause_uncertain_turbine_problem_is_unclear():
    j = Judgments(answers(names_routine=NO, names_outside_condition=NO, names_turbine_problem=BETWEEN))
    assert derive_cause(j, status="Stop") == UNCLEAR
    assert j.uncertain == ["names_turbine_problem"]


def test_derive_cause_stops_at_first_decision_a_confident_yes_never_reads_later_questions():
    j = Judgments(answers(names_routine=YES, names_outside_condition=BETWEEN, names_turbine_problem=BETWEEN))
    assert derive_cause(j, status="Stop") == PLANNED
    assert j.read == ["names_routine"]
    assert j.uncertain == []


# --- apply_step1_rules(): each triage rule ------------------------------------------


def test_rule_safety_hazard_yes_is_act_now():
    j = Judgments(answers(names_safety_hazard=YES, names_physical_damage=NO, names_routine=YES))
    assert apply_step1_rules(j, status="Stop") == (ACT_NOW, ["safety"], PLANNED)


def test_rule_physical_damage_yes_is_act_now():
    j = Judgments(
        answers(
            names_safety_hazard=NO,
            names_physical_damage=YES,
            names_routine=NO,
            names_outside_condition=NO,
            names_turbine_problem=YES,
        )
    )
    assert apply_step1_rules(j, status="Stop") == (ACT_NOW, ["damaged part"], FAULT)


def test_rule_an_uncertain_read_is_monitor_with_its_id():
    j = Judgments(answers(names_safety_hazard=NO, names_physical_damage=NO, names_routine=BETWEEN))
    assert apply_step1_rules(j, status="Stop") == (MONITOR, ["uncertain: names_routine"], UNCLEAR)


def test_rule_multiple_uncertain_reads_list_every_id_in_read_order():
    j = Judgments(answers(names_safety_hazard=BETWEEN, names_physical_damage=BETWEEN, names_routine=NO, names_outside_condition=NO, names_turbine_problem=NO))
    assert apply_step1_rules(j, status="Stop") == (
        MONITOR,
        ["uncertain: names_safety_hazard, names_physical_damage"],
        UNCLEAR,
    )


def test_rule_cause_unclear_with_no_uncertain_read_is_monitor_cause_unclear():
    j = Judgments(
        answers(
            names_safety_hazard=NO,
            names_physical_damage=NO,
            names_routine=NO,
            names_outside_condition=NO,
            names_turbine_problem=NO,
        )
    )
    assert apply_step1_rules(j, status="Stop") == (MONITOR, ["cause unclear"], UNCLEAR)


def test_rule_fault_is_monitor_remote_reset_may_clear_it():
    j = Judgments(
        answers(
            names_safety_hazard=NO,
            names_physical_damage=NO,
            names_routine=NO,
            names_outside_condition=NO,
            names_turbine_problem=YES,
        )
    )
    assert apply_step1_rules(j, status="Stop") == (MONITOR, ["fault, remote reset may clear it"], FAULT)


def test_rule_running_while_warning_is_monitor():
    j = Judgments(
        answers(
            names_safety_hazard=NO,
            names_physical_damage=NO,
            names_routine=NO,
            names_outside_condition=NO,
            names_turbine_problem=NO,
        )
    )
    assert apply_step1_rules(j, status="Warning") == (MONITOR, ["warning while running"], RUNNING)


def test_rule_planned_is_no_action():
    j = Judgments(answers(names_safety_hazard=NO, names_physical_damage=NO, names_routine=YES))
    assert apply_step1_rules(j, status="Stop") == (NO_ACTION, ["planned"], PLANNED)


def test_rule_external_is_no_action():
    j = Judgments(answers(names_safety_hazard=NO, names_physical_damage=NO, names_routine=NO, names_outside_condition=YES))
    assert apply_step1_rules(j, status="Warning") == (NO_ACTION, ["external"], EXTERNAL)


# --- apply_step1_rules(): safety and physical damage win even over uncertainty -----


def test_confident_safety_yes_wins_over_an_uncertain_cause_chain():
    j = Judgments(answers(names_safety_hazard=YES, names_physical_damage=NO, names_routine=BETWEEN))
    triage_class, reasons, _cause = apply_step1_rules(j, status="Stop")
    assert (triage_class, reasons) == (ACT_NOW, ["safety"])


def test_confident_physical_damage_yes_wins_over_an_uncertain_cause_chain():
    j = Judgments(answers(names_safety_hazard=NO, names_physical_damage=YES, names_routine=BETWEEN))
    triage_class, reasons, _cause = apply_step1_rules(j, status="Stop")
    assert (triage_class, reasons) == (ACT_NOW, ["damaged part"])


def test_confident_safety_yes_wins_even_when_physical_damage_is_itself_uncertain():
    j = Judgments(answers(names_safety_hazard=YES, names_physical_damage=BETWEEN, names_routine=YES))
    triage_class, reasons, _cause = apply_step1_rules(j, status="Stop")
    assert (triage_class, reasons) == (ACT_NOW, ["safety"])


# --- triage(): Jev is skipped for Informational events ------------------------------


def test_jev_is_not_asked_for_informational_events():
    events = [ev(status="Informational", message="Substation grid failure")]
    fake = FakeJev()
    results = run(events, fake)

    assert fake.calls == []
    assert results[0].triage == NO_ACTION
    assert results[0].reasons == ["informational"]
    assert results[0].cause is None


def test_chattering_informational_event_becomes_monitor():
    events = [
        ev(status="Informational", message="Substation grid failure", start=T0),
        ev(status="Informational", message="Substation grid failure", start=T0 + timedelta(minutes=1)),
        ev(status="Informational", message="Substation grid failure", start=T0 + timedelta(minutes=2)),
    ]
    fake = FakeJev()
    results = run(events, fake)

    assert fake.calls == []
    for result in results:
        assert result.triage == MONITOR
        assert result.reasons == ["chattering"]
        assert result.chattering is True
        assert result.cause is None


# --- triage(): the cache, one Jev request per distinct (status, message) pair ------


def test_one_call_per_distinct_status_message_pair():
    events = [
        ev(status="Warning", message="Pitch runtime error", start=T0),
        ev(status="Warning", message="Pitch runtime error", start=T0 + timedelta(minutes=20)),
        ev(status="Warning", message="Pitch runtime error", start=T0 + timedelta(minutes=40)),
        ev(status="Stop", message="Emergency stop nacelle", start=T0 + timedelta(minutes=60)),
    ]
    fake = FakeJev(values=dict(names_safety_hazard=NO, names_physical_damage=NO, names_routine=YES))
    cache = {}
    run(events, fake, cache)

    assert len(fake.calls) == 2
    assert {call["state"]["message"] for call in fake.calls} == {"Pitch runtime error", "Emergency stop nacelle"}
    assert set(cache) == {"Warning", "Stop"}
    assert set(cache["Warning"]) == {"Pitch runtime error"}
    assert set(cache["Stop"]) == {"Emergency stop nacelle"}


def test_same_message_under_a_different_status_is_a_different_pair():
    events = [
        ev(status="Warning", message="Generator temperature high"),
        ev(status="Stop", message="Generator temperature high"),
    ]
    fake = FakeJev(values=dict(names_safety_hazard=NO, names_physical_damage=NO, names_routine=YES))
    run(events, fake)

    assert len(fake.calls) == 2


def test_jev_state_carries_only_status_and_message():
    events = [ev(status="Stop", message="Emergency stop nacelle")]
    fake = FakeJev(values=dict(names_safety_hazard=YES, names_physical_damage=NO, names_routine=NO))
    run(events, fake)

    assert fake.calls[0]["state"] == {"status": "Stop", "message": "Emergency stop nacelle"}
    assert set(fake.calls[0]["questions"]) == {
        "names_safety_hazard",
        "names_physical_damage",
        "names_routine",
        "names_outside_condition",
        "names_turbine_problem",
    }


def test_cache_can_be_pre_seeded_so_jev_is_not_asked_again():
    events = [ev(status="Stop", message="Emergency stop nacelle")]
    cache = {
        "Stop": {
            "Emergency stop nacelle": answers(
                names_safety_hazard=YES, names_physical_damage=NO, names_routine=NO, names_outside_condition=NO, names_turbine_problem=NO
            )
        }
    }
    # An unconfigured FakeJev would answer differently (every noul defaults to 0.05,
    # a confident no), so a hit proves the cache was used.
    fake = FakeJev()
    results = run(events, fake, cache)

    assert fake.calls == []
    assert results[0].triage == ACT_NOW
    assert results[0].reasons == ["safety"]


# --- triage(): a FakeJev answer for a question that is never read must not count ---


def test_an_uncertain_answer_on_an_unread_question_does_not_affect_the_result():
    events = [ev(status="Stop", message="Cable unwind")]
    fake = FakeJev(
        values=dict(
            names_safety_hazard=NO,
            names_physical_damage=NO,
            names_routine=YES,  # decides the cause immediately: planned
            names_outside_condition=BETWEEN,  # never read: routine already decided
            names_turbine_problem=BETWEEN,  # never read either
        )
    )
    results = run(events, fake)

    assert results[0].triage == NO_ACTION
    assert results[0].reasons == ["planned"]
    assert results[0].cause == PLANNED


# --- triage(): a long stop gets its reason and at least monitor --------------------


def test_long_stop_floors_no_action_up_to_monitor():
    events = [ev(status="Stop", message="Cable unwind", duration=25 * 3600)]
    fake = FakeJev(values=dict(names_safety_hazard=NO, names_physical_damage=NO, names_routine=YES))
    results = run(events, fake)

    assert results[0].triage == MONITOR
    assert results[0].reasons == ["planned", "long stop"]


def test_long_stop_leaves_an_already_higher_triage_alone_but_still_adds_the_reason():
    events = [ev(status="Stop", message="Gearbox bearing worn", duration=30 * 3600)]
    fake = FakeJev(
        values=dict(
            names_safety_hazard=NO,
            names_physical_damage=YES,
            names_routine=NO,
            names_outside_condition=NO,
            names_turbine_problem=YES,
        )
    )
    results = run(events, fake)

    assert results[0].triage == ACT_NOW
    assert results[0].reasons == ["damaged part", "long stop"]


def test_a_short_stop_is_not_marked_long_stop():
    events = [ev(status="Stop", message="Cable unwind", duration=23 * 3600)]
    fake = FakeJev(values=dict(names_safety_hazard=NO, names_physical_damage=NO, names_routine=YES))
    results = run(events, fake)

    assert results[0].triage == NO_ACTION
    assert results[0].reasons == ["planned"]


# --- triage(): a long stop and chattering can combine on the one event that is both ---


def test_long_stop_and_chattering_combine_on_the_one_event_that_is_both():
    events = [
        ev(status="Stop", message="Manual stop", start=T0, duration=30 * 3600),
        ev(status="Stop", message="Manual stop", start=T0 + timedelta(minutes=1)),
        ev(status="Stop", message="Manual stop", start=T0 + timedelta(minutes=2)),
    ]
    fake = FakeJev(values=dict(names_safety_hazard=NO, names_physical_damage=NO, names_routine=YES))
    results = run(events, fake)

    long_and_chattering, chattering_only, _ = results
    assert long_and_chattering.chattering is True
    assert long_and_chattering.triage == MONITOR
    assert long_and_chattering.reasons == ["planned", "chattering", "long stop"]

    assert chattering_only.chattering is True
    assert chattering_only.triage == NO_ACTION
    assert chattering_only.reasons == ["planned", "chattering"]


# --- triage(): chattering on a non-informational event adds a reason, not a class change ---


def test_chattering_non_informational_event_keeps_its_class_and_adds_a_reason():
    events = [
        ev(status="Stop", message="Manual stop", start=T0),
        ev(status="Stop", message="Manual stop", start=T0 + timedelta(minutes=1)),
        ev(status="Stop", message="Manual stop", start=T0 + timedelta(minutes=2)),
    ]
    fake = FakeJev(values=dict(names_safety_hazard=NO, names_physical_damage=NO, names_routine=YES))
    results = run(events, fake)

    for result in results:
        assert result.chattering is True
        assert result.triage == NO_ACTION
        assert result.reasons == ["planned", "chattering"]


# --- triage(): flood only sets the flag, it does not change class or reasons -------


def test_flood_only_flags_the_events_it_does_not_change_their_triage():
    events = [ev(status="Warning", message=f"m{i}", start=T0 + timedelta(seconds=i)) for i in range(11)]
    fake = FakeJev(values=dict(names_safety_hazard=NO, names_physical_damage=NO, names_routine=YES))
    results = run(events, fake)

    assert all(result.flood is True for result in results)
    assert all(result.triage == NO_ACTION and result.reasons == ["planned"] for result in results)


# --- triage(): results line up with events, one per event, same order --------------


def test_results_are_returned_in_event_order():
    events = [
        ev(status="Informational", message="a", start=T0),
        ev(status="Stop", message="b", start=T0 + timedelta(hours=1)),
        ev(status="Warning", message="c", start=T0 + timedelta(hours=2)),
    ]
    fake = FakeJev(values=dict(names_safety_hazard=NO, names_physical_damage=NO, names_routine=YES))
    results = run(events, fake)

    assert [r.message for r in results] == ["a", "b", "c"]
    assert [r.turbine for r in results] == [e.turbine for e in events]
    assert [r.status for r in results] == [e.status for e in events]
