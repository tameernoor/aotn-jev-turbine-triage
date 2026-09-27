from fakes import answers

from jev_turbine.judgments import Judgments


def test_yes_reads_a_noul_at_or_above_the_yes_threshold():
    j = Judgments(answers(safety_related=0.93))
    assert j.yes("safety_related") is True
    assert j.read == ["safety_related"]
    assert j.uncertain == []


def test_no_reads_a_noul_at_or_below_the_no_threshold():
    j = Judgments(answers(safety_related=0.1))
    assert j.no("safety_related") is True
    assert j.yes("safety_related") is False
    assert j.uncertain == []


def test_middle_values_are_recorded_as_uncertain_once():
    j = Judgments(answers(needs_site_visit=0.5))
    assert j.yes("needs_site_visit") is False
    assert j.no("needs_site_visit") is False
    assert j.uncertain == ["needs_site_visit"]
    assert j.read == ["needs_site_visit"]


def test_thresholds_are_inclusive():
    j = Judgments(answers(a=0.8, b=0.2))
    assert j.yes("a") is True
    assert j.no("b") is True
    assert j.uncertain == []


def test_unread_answers_are_not_recorded():
    j = Judgments(answers(safety_related=0.5, needs_site_visit=0.95))
    assert j.yes("needs_site_visit") is True
    assert j.read == ["needs_site_visit"]
    assert j.uncertain == []


def test_choice_is_read():
    j = Judgments(answers(cause="fault"))
    assert j.choice("cause") == "fault"
    assert j.read == ["cause"]


def test_low_confidence_choice_is_uncertain():
    j = Judgments(
        answers(
            cause={
                "type": "choice",
                "value": "fault",
                "probabilities": {"fault": 0.5, "planned": 0.5},
                "confidence": 0.5,
            }
        )
    )
    assert j.choice("cause") == "fault"
    assert j.uncertain == ["cause"]
    assert j.read == ["cause"]


def test_high_confidence_choice_is_not_uncertain():
    j = Judgments(answers(cause="fault"))
    assert j.choice("cause") == "fault"
    assert j.uncertain == []


def test_choice_confidence_threshold_is_inclusive():
    j = Judgments(
        answers(cause={"type": "choice", "value": "fault", "probabilities": {"fault": 0.6}, "confidence": 0.6})
    )
    assert j.choice("cause") == "fault"
    assert j.uncertain == []


def test_a_reading_that_is_itself_uncertain_is_recorded_only_once_even_if_read_twice():
    j = Judgments(answers(safety_related=0.5))
    j.yes("safety_related")
    j.no("safety_related")
    assert j.uncertain == ["safety_related"]
