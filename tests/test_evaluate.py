from datetime import datetime, timezone

from jev_turbine.evaluate import IEC_TO_CAUSE, evaluate
from jev_turbine.models import Event

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


def cause_judgment(value, probabilities=None, confidence=1.0):
    return {"cause": {"type": "choice", "value": value, "probabilities": probabilities or {value: confidence}, "confidence": confidence}}


def cache_with(*pairs):
    """pairs: (status, message, cause_judgment_dict)."""
    cache: dict = {}
    for status, message, judgment in pairs:
        cache.setdefault(status, {})[message] = judgment
    return cache


# --- the mapping itself ---


def test_iec_to_cause_matches_the_plan():
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


def test_uncertain_messages_are_counted_separately_from_accuracy():
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

    assert result["uncertain_messages"] == 1
    assert result["accuracy_by_message"] == {"correct": 2, "total": 2, "accuracy": 1.0}
