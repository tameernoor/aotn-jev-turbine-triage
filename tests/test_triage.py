import asyncio
from datetime import datetime, timedelta, timezone

from fakes import FakeJev, answers

from jev_turbine.judgments import Judgments
from jev_turbine.models import Event
from jev_turbine.triage import ACT_NOW, MONITOR, NO_ACTION, apply_rules, triage

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


# --- apply_rules: one rule branch at a time, no Jev involved ---


def test_rule2_uncertain_cause_sends_to_monitor():
    j = Judgments(answers(cause={"type": "choice", "value": "fault", "probabilities": {"fault": 0.5}, "confidence": 0.5}, safety_related=0.1))
    assert apply_rules(j, status="Stop") == (MONITOR, ["uncertain: cause"])


def test_rule2_uncertain_safety_related_sends_to_monitor():
    j = Judgments(answers(cause="planned", safety_related=0.5))
    assert apply_rules(j, status="Stop") == (MONITOR, ["uncertain: safety_related"])


def test_rule2_both_uncertain_lists_both_ids_in_read_order():
    j = Judgments(
        answers(
            cause={"type": "choice", "value": "fault", "probabilities": {"fault": 0.5}, "confidence": 0.5},
            safety_related=0.5,
        )
    )
    assert apply_rules(j, status="Stop") == (MONITOR, ["uncertain: cause, safety_related"])


def test_rule2_uncertain_cause_wins_over_a_confident_safety_yes():
    # Rule 2 is checked before rule 3, so an uncertain cause overrides even a clear
    # safety_related yes.
    j = Judgments(
        answers(
            cause={"type": "choice", "value": "fault", "probabilities": {"fault": 0.5}, "confidence": 0.5},
            safety_related=0.95,
        )
    )
    assert apply_rules(j, status="Stop") == (MONITOR, ["uncertain: cause"])


def test_rule3_safety_related_yes_is_act_now():
    j = Judgments(answers(cause="planned", safety_related=0.9))
    assert apply_rules(j, status="Stop") == (ACT_NOW, ["safety"])


def test_rule4_fault_needing_a_site_visit_is_act_now():
    j = Judgments(answers(cause="fault", safety_related=0.1, needs_site_visit=0.9))
    assert apply_rules(j, status="Stop") == (ACT_NOW, ["fault needing a site visit"])


def test_rule5_fault_not_needing_a_site_visit_is_monitor():
    j = Judgments(answers(cause="fault", safety_related=0.1, needs_site_visit=0.1))
    assert apply_rules(j, status="Stop") == (MONITOR, ["fault, remote reset may clear it"])


def test_rule6_running_while_warning_is_monitor():
    j = Judgments(answers(cause="running", safety_related=0.1))
    assert apply_rules(j, status="Warning") == (MONITOR, ["warning while running"])


def test_rule6_does_not_fire_for_running_on_a_non_warning_status():
    j = Judgments(answers(cause="running", safety_related=0.1))
    assert apply_rules(j, status="Communication") == (NO_ACTION, ["running"])


def test_rule7_planned_is_no_action():
    j = Judgments(answers(cause="planned", safety_related=0.1))
    assert apply_rules(j, status="Stop") == (NO_ACTION, ["planned"])


def test_rule7_external_is_no_action():
    j = Judgments(answers(cause="external", safety_related=0.1))
    assert apply_rules(j, status="Warning") == (NO_ACTION, ["external"])


# --- fan-out: needs_site_visit is read only when cause is fault ---


def test_needs_site_visit_is_read_for_a_fault():
    j = Judgments(answers(cause="fault", safety_related=0.1, needs_site_visit=0.9))
    apply_rules(j, status="Stop")
    assert "needs_site_visit" in j.read


def test_needs_site_visit_is_not_read_for_a_non_fault_cause():
    j = Judgments(answers(cause="planned", safety_related=0.1, needs_site_visit=0.9))
    apply_rules(j, status="Stop")
    assert "needs_site_visit" not in j.read


def test_needs_site_visit_is_not_read_when_safety_related_already_decided_the_event():
    j = Judgments(answers(cause="fault", safety_related=0.9, needs_site_visit=0.9))
    apply_rules(j, status="Stop")
    assert "needs_site_visit" not in j.read


# --- triage(): Jev is skipped for Informational events ---


def test_jev_is_not_asked_for_informational_events():
    events = [ev(status="Informational", message="Substation grid failure")]
    fake = FakeJev()
    results = run(events, fake)

    assert fake.calls == []
    assert results[0].triage == NO_ACTION
    assert results[0].reasons == ["informational"]


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


# --- triage(): the cache, one Jev request per distinct (status, message) pair ---


def test_one_call_per_distinct_status_message_pair():
    events = [
        ev(status="Warning", message="Pitch runtime error", start=T0),
        ev(status="Warning", message="Pitch runtime error", start=T0 + timedelta(minutes=20)),
        ev(status="Warning", message="Pitch runtime error", start=T0 + timedelta(minutes=40)),
        ev(status="Stop", message="Emergency stop nacelle", start=T0 + timedelta(minutes=60)),
    ]
    fake = FakeJev(values=dict(cause="planned", safety_related=0.1))
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
    fake = FakeJev(values=dict(cause="planned", safety_related=0.1))
    run(events, fake)

    assert len(fake.calls) == 2


def test_jev_state_carries_only_status_and_message():
    events = [ev(status="Stop", message="Emergency stop nacelle")]
    fake = FakeJev(values=dict(cause="fault", safety_related=0.1, needs_site_visit=0.9))
    run(events, fake)

    assert fake.calls[0]["state"] == {"status": "Stop", "message": "Emergency stop nacelle"}
    assert set(fake.calls[0]["questions"]) == {"cause", "safety_related", "needs_site_visit"}


def test_cache_can_be_pre_seeded_so_jev_is_not_asked_again():
    events = [ev(status="Stop", message="Emergency stop nacelle")]
    cache = {"Stop": {"Emergency stop nacelle": answers(cause="fault", safety_related=0.9, needs_site_visit=0.1)}}
    # An unconfigured FakeJev would answer differently (cause defaults to the first
    # criterion, "fault", with every noul at 0.05), so a hit proves the cache was used.
    fake = FakeJev()
    results = run(events, fake, cache)

    assert fake.calls == []
    assert results[0].triage == ACT_NOW
    assert results[0].reasons == ["safety"]


# --- triage(): a long stop gets its reason and at least monitor ---


def test_long_stop_floors_no_action_up_to_monitor():
    events = [ev(status="Stop", message="Cable unwind", duration=25 * 3600)]
    fake = FakeJev(values=dict(cause="planned", safety_related=0.1))
    results = run(events, fake)

    assert results[0].triage == MONITOR
    assert results[0].reasons == ["planned", "long stop"]


def test_long_stop_leaves_an_already_higher_triage_alone_but_still_adds_the_reason():
    events = [ev(status="Stop", message="Gearbox bearing fault", duration=30 * 3600)]
    fake = FakeJev(values=dict(cause="fault", safety_related=0.1, needs_site_visit=0.1))
    results = run(events, fake)

    assert results[0].triage == MONITOR
    assert results[0].reasons == ["fault, remote reset may clear it", "long stop"]


def test_a_short_stop_is_not_marked_long_stop():
    events = [ev(status="Stop", message="Cable unwind", duration=23 * 3600)]
    fake = FakeJev(values=dict(cause="planned", safety_related=0.1))
    results = run(events, fake)

    assert results[0].triage == NO_ACTION
    assert results[0].reasons == ["planned"]


# --- triage(): chattering on a non-informational event adds a reason, not a class change ---


def test_chattering_non_informational_event_keeps_its_class_and_adds_a_reason():
    events = [
        ev(status="Stop", message="Manual stop", start=T0),
        ev(status="Stop", message="Manual stop", start=T0 + timedelta(minutes=1)),
        ev(status="Stop", message="Manual stop", start=T0 + timedelta(minutes=2)),
    ]
    fake = FakeJev(values=dict(cause="planned", safety_related=0.1))
    results = run(events, fake)

    for result in results:
        assert result.chattering is True
        assert result.triage == NO_ACTION
        assert result.reasons == ["planned", "chattering"]


# --- triage(): flood only sets the flag, it does not change class or reasons ---


def test_flood_only_flags_the_events_it_does_not_change_their_triage():
    events = [ev(status="Warning", message=f"m{i}", start=T0 + timedelta(seconds=i)) for i in range(11)]
    fake = FakeJev(values=dict(cause="planned", safety_related=0.1))
    results = run(events, fake)

    assert all(result.flood is True for result in results)
    assert all(result.triage == NO_ACTION and result.reasons == ["planned"] for result in results)


# --- triage(): results line up with events, one per event, same order ---


def test_results_are_returned_in_event_order():
    events = [
        ev(status="Informational", message="a", start=T0),
        ev(status="Stop", message="b", start=T0 + timedelta(hours=1)),
        ev(status="Warning", message="c", start=T0 + timedelta(hours=2)),
    ]
    fake = FakeJev(values=dict(cause="planned", safety_related=0.1))
    results = run(events, fake)

    assert [r.message for r in results] == ["a", "b", "c"]
    assert [r.turbine for r in results] == [e.turbine for e in events]
    assert [r.status for r in results] == [e.status for e in events]
