from datetime import datetime, timezone

from fakes import answers

from jev_turbine.evaluate import IEC_TO_CAUSE, evaluate
from jev_turbine.models import Event
from jev_turbine.triage import ACT_NOW, EXTERNAL, FAULT, MONITOR, NO_ACTION, PLANNED, RUNNING, UNCLEAR, TriageResult

T0 = datetime(2016, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

# A confident "yes" / "no" for a noul, and a value strictly between the two (uncertain),
# matching judgments.py's YES/NO thresholds (0.8 / 0.2 inclusive).
YES = 0.9
NO = 0.1
BETWEEN = 0.5


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
    # Step 2 (escalate.py) is untouched in Task 1: it still re-asks Jev the old
    # three-question set, so its own judgments dict still carries a raw "cause" choice.
    return {"cause": {"type": "choice", "value": value, "probabilities": {value: 1.0}, "confidence": 1.0}}


def cause_raw(cause, uncertain=False):
    """A raw step-1 judgments dict (the three cause-chain questions from
    questions/event.yaml) that triage.derive_cause turns into `cause`. For RUNNING, the
    caller must also give the event status="Warning" (derive_cause only returns RUNNING
    for that status); for UNCLEAR from three confident no's, any other status. `uncertain
    =True` (only meaningful for UNCLEAR) makes names_routine itself the uncertain read
    that stops the chain, instead of three confident no's."""
    if cause == PLANNED:
        return answers(names_routine=YES)
    if cause == EXTERNAL:
        return answers(names_routine=NO, names_outside_condition=YES)
    if cause == FAULT:
        return answers(names_routine=NO, names_outside_condition=NO, names_turbine_problem=YES)
    if cause == RUNNING:
        return answers(names_routine=NO, names_outside_condition=NO, names_turbine_problem=NO)
    if cause == UNCLEAR:
        if uncertain:
            return answers(names_routine=BETWEEN)
        return answers(names_routine=NO, names_outside_condition=NO, names_turbine_problem=NO)
    raise ValueError(cause)


def cache_with(*pairs):
    """pairs: (status, message, raw_judgments_dict)."""
    cache: dict = {}
    for status, message, raw in pairs:
        cache.setdefault(status, {})[message] = raw
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
        # message "Gearbox fault": 3 events, derived cause correctly "fault" every time
        ev(status="Stop", message="Gearbox fault", iec_category="Forced outage"),
        ev(status="Stop", message="Gearbox fault", iec_category="Forced outage"),
        ev(status="Stop", message="Gearbox fault", iec_category="Forced outage"),
        # message "Cable unwind": 1 event, derived cause wrongly "fault" instead of "planned"
        ev(status="Stop", message="Cable unwind", iec_category="Scheduled Maintenance"),
    ]
    cache = cache_with(
        ("Stop", "Gearbox fault", cause_raw(FAULT)),
        ("Stop", "Cable unwind", cause_raw(FAULT)),
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
    cache = cache_with(("Stop", "Cable unwind", cause_raw(PLANNED)))

    result = evaluate(events, cache)

    assert result["events_evaluated"] == 0
    assert result["accuracy_by_event"]["total"] == 0
    assert result["accuracy_by_event"]["accuracy"] is None


def test_informational_events_are_excluded_even_with_a_category():
    events = [ev(status="Informational", message="System OK", iec_category="Full Performance")]
    cache = cache_with(("Informational", "System OK", cause_raw(RUNNING)))

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
        ("Stop", "Gearbox fault", cause_raw(FAULT)),
        ("Stop", "Cable unwind", cause_raw(FAULT)),
    )

    result = evaluate(events, cache)

    assert result["confusion_matrix"] == {
        "fault": {"fault": 1},
        "planned": {"fault": 2},
    }


def test_confusion_matrix_has_an_unclear_column():
    events = [ev(status="Stop", message="Cable unwind", iec_category="Scheduled Maintenance")]
    cache = cache_with(("Stop", "Cable unwind", cause_raw(UNCLEAR)))

    result = evaluate(events, cache)

    assert result["confusion_matrix"] == {"planned": {"unclear": 1}}


# --- disagreements ---


def test_disagreements_list_distinct_messages_with_the_ids_that_were_uncertain():
    events = [
        ev(status="Stop", message="Gearbox fault", iec_category="Forced outage"),
        ev(status="Stop", message="Cable unwind", iec_category="Scheduled Maintenance"),
    ]
    cache = cache_with(
        ("Stop", "Gearbox fault", cause_raw(FAULT)),
        ("Stop", "Cable unwind", cause_raw(UNCLEAR, uncertain=True)),
    )

    result = evaluate(events, cache)

    assert result["disagreements"] == [
        {
            "status": "Stop",
            "message": "Cable unwind",
            "expected": "planned",
            "got": "unclear",
            "uncertain": ["names_routine"],
        }
    ]


def test_agreements_are_not_listed_as_disagreements():
    events = [ev(status="Stop", message="Gearbox fault", iec_category="Forced outage")]
    cache = cache_with(("Stop", "Gearbox fault", cause_raw(FAULT)))

    result = evaluate(events, cache)

    assert result["disagreements"] == []


# --- unclear causes ---


def test_causes_unclear_evaluated_is_counted_separately_from_accuracy():
    events = [
        ev(status="Stop", message="Gearbox fault", iec_category="Forced outage"),
        ev(status="Stop", message="Cable unwind", iec_category="Scheduled Maintenance"),
    ]
    cache = cache_with(
        ("Stop", "Gearbox fault", cause_raw(FAULT)),
        ("Stop", "Cable unwind", cause_raw(UNCLEAR)),
    )

    result = evaluate(events, cache)

    assert result["causes_unclear"]["evaluated"] == 1
    # unclear is not planned, so it counts as wrong, not skipped
    assert result["accuracy_by_message"] == {"correct": 1, "total": 2, "accuracy": 0.5}


def test_causes_unclear_asked_covers_every_pair_in_the_cache_not_only_scored_ones():
    # "Cable unwind" is IEC-scored and confidently planned; "Unlogged noise" has no
    # matching event at all (never scored) but is still a pair Jev was asked about, and
    # its derived cause is unclear; "Gearbox fault" is IEC-scored and itself unclear.
    events = [
        ev(status="Stop", message="Cable unwind", iec_category="Scheduled Maintenance"),
        ev(status="Stop", message="Gearbox fault", iec_category="Forced outage"),
    ]
    cache = cache_with(
        ("Stop", "Cable unwind", cause_raw(PLANNED)),
        ("Stop", "Gearbox fault", cause_raw(UNCLEAR)),
        ("Stop", "Unlogged noise", cause_raw(UNCLEAR)),
    )

    result = evaluate(events, cache)

    assert result["causes_unclear"]["evaluated"] == 1  # only "Gearbox fault" is IEC-scored and unclear
    assert result["causes_unclear"]["asked"] == 2  # "Gearbox fault" and "Unlogged noise" both


# --- step 2: with-context accuracy, escalation deltas, before/after triage counts ---


def test_results_none_reports_the_same_numbers_as_step1_alone():
    events = [ev(status="Stop", message="Gearbox fault", iec_category="Forced outage")]
    cache = cache_with(("Stop", "Gearbox fault", cause_raw(FAULT)))

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
    cache = cache_with(("Stop", "Gearbox fault", cause_raw(FAULT)))
    results = [result_for(event, triage=MONITOR, reasons=["fault, remote reset may clear it"])]  # never escalated

    result = evaluate([event], cache, results)

    assert result["accuracy_by_event_with_context"] == {"correct": 1, "total": 1, "accuracy": 1.0}
    assert result["escalated_with_iec_category"]["evaluated"] == 0


def test_escalated_event_wrong_to_right():
    event = ev(status="Stop", message="Manual stop", iec_category="Forced outage")  # expected: fault
    cache = cache_with(("Stop", "Manual stop", cause_raw(PLANNED)))  # step 1 was wrong
    results = [
        result_for(
            event,
            triage=MONITOR,
            reasons=["fault, remote reset may clear it"],
            step1_triage=NO_ACTION,
            step1_reasons=["planned"],
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
    cache = cache_with(("Stop", "Gearbox fault", cause_raw(FAULT)))  # step 1 was right
    results = [
        result_for(
            event,
            triage=NO_ACTION,
            reasons=["planned"],
            step1_triage=MONITOR,
            step1_reasons=["uncertain: names_routine"],
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
    cache = cache_with(("Stop", "Gearbox fault", cause_raw(FAULT)))
    results = [
        result_for(
            event,
            triage=ACT_NOW,
            reasons=["fault needing a site visit"],
            step1_triage=MONITOR,
            step1_reasons=["uncertain: names_turbine_problem"],
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
    cache = cache_with(("Stop", "Gearbox fault", cause_raw(FAULT)))
    results = [
        result_for(
            event,
            triage=MONITOR,
            reasons=["uncertain: needs_site_visit"],  # step 2's own uncertain reason
            step1_triage=MONITOR,
            step1_reasons=["uncertain: names_turbine_problem"],
            step2_judgments=step2_cause("fault"),
        )
    ]

    result = evaluate([event], cache, results)

    assert result["escalated_with_iec_category"]["still_uncertain"] == 1


def test_triage_counts_before_and_after_context_over_every_event_not_only_iec_scored():
    escalated = ev(status="Stop", message="Gearbox fault", iec_category="Forced outage")
    not_escalated = ev(status="Stop", message="Cable unwind", iec_category=None)
    cache = cache_with(("Stop", "Gearbox fault", cause_raw(FAULT)))
    results = [
        result_for(
            escalated,
            triage=ACT_NOW,
            reasons=["fault needing a site visit"],
            step1_triage=MONITOR,
            step1_reasons=["uncertain: names_turbine_problem"],
            step2_judgments=step2_cause("fault"),
        ),
        result_for(not_escalated, triage=NO_ACTION, reasons=["planned"]),  # never touched by step 2
    ]

    result = evaluate([escalated, not_escalated], cache, results)

    assert result["triage_counts_before_context"] == {MONITOR: 1, NO_ACTION: 1}
    assert result["triage_counts_after_context"] == {ACT_NOW: 1, NO_ACTION: 1}


def test_escalated_event_without_iec_category_is_excluded_from_escalation_counts():
    event = ev(status="Stop", message="No category", iec_category=None)
    cache = cache_with(("Stop", "No category", cause_raw(PLANNED)))
    results = [
        result_for(
            event,
            triage=NO_ACTION,
            reasons=["external"],
            step1_triage=MONITOR,
            step1_reasons=["uncertain: names_routine"],
            step2_judgments=step2_cause("external"),
        )
    ]

    result = evaluate([event], cache, results)

    assert result["escalated_with_iec_category"]["evaluated"] == 0
    # still counted in the before/after triage totals even without an IEC category
    assert result["triage_counts_before_context"] == {MONITOR: 1}
    assert result["triage_counts_after_context"] == {NO_ACTION: 1}
