from datetime import datetime, timezone

from jev_turbine.evaluate import IEC_TO_CAUSE, evaluate
from jev_turbine.models import Event
from jev_turbine.triage import ACT_NOW, MONITOR, NO_ACTION, TriageResult

T0 = datetime(2016, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def ev(turbine="Kelmarsh 1", status="Stop", message="msg", iec_category=None):
    return Event(
        turbine=turbine,
        start=T0,
        end=None,
        duration_seconds=None,
        status=status,
        code="1",
        message=message,
        iec_category=iec_category,
    )


def result_for(
    event,
    triage=NO_ACTION,
    reasons=None,
    step1_triage=None,
    step1_reasons=None,
    context=None,
    step2_judgments=None,
):
    """A TriageResult for `event`, step-1-only unless step1_triage is given
    (matching how escalate() marks an escalated event: step1_triage/reasons
    hold step 1's own read, triage/reasons hold the final one)."""
    return TriageResult(
        turbine=event.turbine,
        start=event.start,
        end=event.end,
        duration_seconds=event.duration_seconds,
        status=event.status,
        message=event.message,
        triage=triage,
        reasons=reasons or [],
        chattering=False,
        flood=False,
        step1_triage=step1_triage,
        step1_reasons=step1_reasons,
        context=context,
        step2_judgments=step2_judgments,
    )


def step2_cause(value):
    return {"cause": {"type": "choice", "value": value, "probabilities": {value: 1.0}, "confidence": 1.0}}


def cause_judgment(value, probabilities=None, confidence=1.0):
    return {"cause": {"type": "choice", "value": value, "probabilities": probabilities or {value: confidence}, "confidence": confidence}}


def cache_with(*pairs):
    """pairs: (status, message, cause_judgment_dict)."""
    cache: dict = {}
    for status, message, judgment in pairs:
        cache.setdefault(status, {})[message] = judgment
    return cache


# --- the mapping itself ---


def test_iec_to_cause_mapping():
    assert IEC_TO_CAUSE == {
        "Forced outage": "fault",
        "Scheduled Maintenance": "planned",
        "Technical Standby": "planned",
        "Requested Shutdown": "planned",
        "Out of Electrical Specification": "external",
        "Out of Environmental Specification": "external",
        "Full Performance": "running",
        "Partial Performance": "running",
    }


# --- accuracy by event vs. by distinct message ---


def test_accuracy_weighs_events_and_messages_differently():
    events = [
        # message "Gearbox fault": 3 events, Jev correctly says fault every time
        ev(status="Stop", message="Gearbox fault", iec_category="Forced outage"),
        ev(status="Stop", message="Gearbox fault", iec_category="Forced outage"),
        ev(status="Stop", message="Gearbox fault", iec_category="Forced outage"),
        # message "Cable unwind": 1 event, Jev wrongly says fault instead of planned
        ev(status="Stop", message="Cable unwind", iec_category="Scheduled Maintenance"),
    ]
    cache = cache_with(
        ("Stop", "Gearbox fault", cause_judgment("fault")),
        ("Stop", "Cable unwind", cause_judgment("fault")),
    )

    result = evaluate(events, cache)

    assert result["accuracy_by_event"] == {"correct": 3, "total": 4, "accuracy": 0.75}
    assert result["accuracy_by_message"] == {"correct": 1, "total": 2, "accuracy": 0.5}
    assert result["events_evaluated"] == 4
    assert result["messages_evaluated"] == 2


def test_note_states_the_mapping_is_ours_and_the_category_is_the_operators():
    result = evaluate([], {})

    assert "ours" in result["note"]
    assert "operator" in result["note"]
    assert result["iec_to_cause"] == IEC_TO_CAUSE


# --- exclusions ---


def test_events_without_an_iec_category_are_excluded():
    events = [ev(status="Stop", message="Cable unwind", iec_category=None)]
    cache = cache_with(("Stop", "Cable unwind", cause_judgment("planned")))

    result = evaluate(events, cache)

    assert result["events_evaluated"] == 0
    assert result["accuracy_by_event"]["total"] == 0
    assert result["accuracy_by_event"]["accuracy"] is None


def test_informational_events_are_excluded_even_with_a_category():
    events = [ev(status="Informational", message="System OK", iec_category="Full Performance")]
    cache = cache_with(("Informational", "System OK", cause_judgment("running")))

    result = evaluate(events, cache)

    assert result["events_evaluated"] == 0


def test_a_pair_missing_from_the_cache_is_silently_excluded():
    events = [ev(status="Stop", message="Cable unwind", iec_category="Scheduled Maintenance")]

    result = evaluate(events, {})

    assert result["events_evaluated"] == 0
    assert result["messages_evaluated"] == 0


# --- confusion matrix ---


def test_confusion_matrix_counts_expected_against_got_per_event():
    events = [
        ev(status="Stop", message="Gearbox fault", iec_category="Forced outage"),
        ev(status="Stop", message="Cable unwind", iec_category="Scheduled Maintenance"),
        ev(status="Stop", message="Cable unwind", iec_category="Scheduled Maintenance"),
    ]
    cache = cache_with(
        ("Stop", "Gearbox fault", cause_judgment("fault")),
        ("Stop", "Cable unwind", cause_judgment("fault")),
    )

    result = evaluate(events, cache)

    assert result["confusion_matrix"] == {
        "fault": {"fault": 1},
        "planned": {"fault": 2},
    }


# --- disagreements ---


def test_disagreements_list_distinct_messages_with_jevs_probabilities():
    events = [
        ev(status="Stop", message="Gearbox fault", iec_category="Forced outage"),
        ev(status="Stop", message="Cable unwind", iec_category="Scheduled Maintenance"),
    ]
    cache = cache_with(
        ("Stop", "Gearbox fault", cause_judgment("fault")),
        ("Stop", "Cable unwind", cause_judgment("fault", probabilities={"fault": 0.7, "planned": 0.3}, confidence=0.7)),
    )

    result = evaluate(events, cache)

    assert result["disagreements"] == [
        {
            "status": "Stop",
            "message": "Cable unwind",
            "expected": "planned",
            "got": "fault",
            "probabilities": {"fault": 0.7, "planned": 0.3},
        }
    ]


def test_agreements_are_not_listed_as_disagreements():
    events = [ev(status="Stop", message="Gearbox fault", iec_category="Forced outage")]
    cache = cache_with(("Stop", "Gearbox fault", cause_judgment("fault")))

    result = evaluate(events, cache)

    assert result["disagreements"] == []


# --- uncertain messages ---


def test_uncertain_messages_evaluated_is_counted_separately_from_accuracy():
    events = [
        ev(status="Stop", message="Gearbox fault", iec_category="Forced outage"),
        ev(status="Stop", message="Cable unwind", iec_category="Scheduled Maintenance"),
    ]
    cache = cache_with(
        ("Stop", "Gearbox fault", cause_judgment("fault", probabilities={"fault": 1.0}, confidence=1.0)),
        # confidence below 0.6: uncertain, but still scored on whatever value it picked
        ("Stop", "Cable unwind", cause_judgment("planned", probabilities={"planned": 0.4, "fault": 0.3}, confidence=0.4)),
    )

    result = evaluate(events, cache)

    assert result["uncertain_messages_evaluated"] == 1
    assert result["accuracy_by_message"] == {"correct": 2, "total": 2, "accuracy": 1.0}


def test_uncertain_messages_asked_covers_every_pair_in_the_cache_not_only_scored_ones():
    # "Cable unwind" is IEC-scored and confidently answered; "Unlogged noise" has no
    # matching event at all (never scored) but is still a pair Jev was asked about, with
    # a low-confidence cause; "Gearbox fault" is IEC-scored and itself uncertain.
    events = [
        ev(status="Stop", message="Cable unwind", iec_category="Scheduled Maintenance"),
        ev(status="Stop", message="Gearbox fault", iec_category="Forced outage"),
    ]
    cache = cache_with(
        ("Stop", "Cable unwind", cause_judgment("planned", confidence=1.0)),
        ("Stop", "Gearbox fault", cause_judgment("fault", probabilities={"fault": 0.5, "planned": 0.5}, confidence=0.5)),
        ("Stop", "Unlogged noise", cause_judgment("external", probabilities={"external": 0.4, "planned": 0.3}, confidence=0.4)),
    )

    result = evaluate(events, cache)

    assert result["uncertain_messages_evaluated"] == 1  # only "Gearbox fault" is IEC-scored and uncertain
    assert result["uncertain_messages_asked"] == 2  # "Gearbox fault" and "Unlogged noise" both


# --- step 2: with-context accuracy, escalation deltas, before/after triage counts ---


def test_results_none_reports_the_same_numbers_as_step1_alone():
    events = [ev(status="Stop", message="Gearbox fault", iec_category="Forced outage")]
    cache = cache_with(("Stop", "Gearbox fault", cause_judgment("fault")))

    result = evaluate(events, cache)

    assert result["accuracy_by_event_with_context"] == result["accuracy_by_event"]
    assert result["escalated_with_iec_category"] == {
        "evaluated": 0,
        "cause_changed": 0,
        "wrong_to_right": 0,
        "right_to_wrong": 0,
        "still_uncertain": 0,
    }
    assert result["triage_counts_before_context"] == {}
    assert result["triage_counts_after_context"] == {}


def test_non_escalated_event_with_results_uses_step1_cause_for_with_context_too():
    event = ev(status="Stop", message="Gearbox fault", iec_category="Forced outage")
    cache = cache_with(("Stop", "Gearbox fault", cause_judgment("fault")))
    results = [result_for(event, triage=NO_ACTION, reasons=["fault"])]  # never escalated

    result = evaluate([event], cache, results)

    assert result["accuracy_by_event_with_context"] == {"correct": 1, "total": 1, "accuracy": 1.0}
    assert result["escalated_with_iec_category"]["evaluated"] == 0


def test_escalated_event_wrong_to_right():
    event = ev(status="Stop", message="Manual stop", iec_category="Forced outage")  # expected: fault
    cache = cache_with(("Stop", "Manual stop", cause_judgment("planned")))  # step 1 was wrong
    results = [
        result_for(
            event,
            triage=MONITOR,
            reasons=["fault, remote reset may clear it"],
            step1_triage=MONITOR,
            step1_reasons=["uncertain: cause"],
            step2_judgments=step2_cause("fault"),  # step 2 is right
        )
    ]

    result = evaluate([event], cache, results)

    assert result["accuracy_by_event"] == {"correct": 0, "total": 1, "accuracy": 0.0}
    assert result["accuracy_by_event_with_context"] == {"correct": 1, "total": 1, "accuracy": 1.0}
    assert result["escalated_with_iec_category"] == {
        "evaluated": 1,
        "cause_changed": 1,
        "wrong_to_right": 1,
        "right_to_wrong": 0,
        "still_uncertain": 0,
    }


def test_escalated_event_right_to_wrong():
    event = ev(status="Stop", message="Gearbox fault", iec_category="Forced outage")  # expected: fault
    cache = cache_with(("Stop", "Gearbox fault", cause_judgment("fault")))  # step 1 was right
    results = [
        result_for(
            event,
            triage=NO_ACTION,
            reasons=["planned"],
            step1_triage=MONITOR,
            step1_reasons=["uncertain: cause"],
            step2_judgments=step2_cause("planned"),  # step 2 is wrong
        )
    ]

    result = evaluate([event], cache, results)

    assert result["accuracy_by_event"] == {"correct": 1, "total": 1, "accuracy": 1.0}
    assert result["accuracy_by_event_with_context"] == {"correct": 0, "total": 1, "accuracy": 0.0}
    assert result["escalated_with_iec_category"] == {
        "evaluated": 1,
        "cause_changed": 1,
        "wrong_to_right": 0,
        "right_to_wrong": 1,
        "still_uncertain": 0,
    }


def test_escalated_event_unchanged_cause_is_not_counted_as_changed():
    event = ev(status="Stop", message="Gearbox fault", iec_category="Forced outage")
    cache = cache_with(("Stop", "Gearbox fault", cause_judgment("fault")))
    results = [
        result_for(
            event,
            triage=ACT_NOW,
            reasons=["fault needing a site visit"],
            step1_triage=MONITOR,
            step1_reasons=["uncertain: needs_site_visit"],
            step2_judgments=step2_cause("fault"),  # same cause both times
        )
    ]

    result = evaluate([event], cache, results)

    assert result["escalated_with_iec_category"]["evaluated"] == 1
    assert result["escalated_with_iec_category"]["cause_changed"] == 0
    assert result["escalated_with_iec_category"]["wrong_to_right"] == 0
    assert result["escalated_with_iec_category"]["right_to_wrong"] == 0


def test_escalated_event_still_uncertain_after_step2():
    event = ev(status="Stop", message="Gearbox fault", iec_category="Forced outage")
    cache = cache_with(("Stop", "Gearbox fault", cause_judgment("fault")))
    results = [
        result_for(
            event,
            triage=MONITOR,
            reasons=["uncertain: needs_site_visit"],  # step 2's own uncertain reason
            step1_triage=MONITOR,
            step1_reasons=["uncertain: cause"],
            step2_judgments=step2_cause("fault"),
        )
    ]

    result = evaluate([event], cache, results)

    assert result["escalated_with_iec_category"]["still_uncertain"] == 1


def test_triage_counts_before_and_after_context_over_every_event_not_only_iec_scored():
    escalated = ev(status="Stop", message="Gearbox fault", iec_category="Forced outage")
    not_escalated = ev(status="Stop", message="Cable unwind", iec_category=None)
    cache = cache_with(("Stop", "Gearbox fault", cause_judgment("fault")))
    results = [
        result_for(
            escalated,
            triage=ACT_NOW,
            reasons=["fault needing a site visit"],
            step1_triage=MONITOR,
            step1_reasons=["uncertain: needs_site_visit"],
            step2_judgments=step2_cause("fault"),
        ),
        result_for(not_escalated, triage=NO_ACTION, reasons=["planned"]),  # never touched by step 2
    ]

    result = evaluate([escalated, not_escalated], cache, results)

    assert result["triage_counts_before_context"] == {MONITOR: 1, NO_ACTION: 1}
    assert result["triage_counts_after_context"] == {ACT_NOW: 1, NO_ACTION: 1}


def test_escalated_event_without_iec_category_is_excluded_from_escalation_counts():
    event = ev(status="Stop", message="No category", iec_category=None)
    cache = cache_with(("Stop", "No category", cause_judgment("planned")))
    results = [
        result_for(
            event,
            triage=NO_ACTION,
            reasons=["external"],
            step1_triage=MONITOR,
            step1_reasons=["uncertain: cause"],
            step2_judgments=step2_cause("external"),
        )
    ]

    result = evaluate([event], cache, results)

    assert result["escalated_with_iec_category"]["evaluated"] == 0
    # still counted in the before/after triage totals even without an IEC category
    assert result["triage_counts_before_context"] == {MONITOR: 1}
    assert result["triage_counts_after_context"] == {NO_ACTION: 1}
