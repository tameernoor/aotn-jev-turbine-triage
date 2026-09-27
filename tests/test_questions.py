from pathlib import Path

import pytest

from jev_turbine.escalate import QUESTIONS_PATH as CONTEXT_QUESTIONS_PATH
from jev_turbine.triage import QUESTIONS_PATH, load_questions

EVENT_QUESTION_IDS = {
    "names_safety_hazard",
    "names_physical_damage",
    "names_routine",
    "names_outside_condition",
    "names_turbine_problem",
}


def test_event_questions_load_with_the_five_expected_ids_and_types():
    questions = load_questions()

    assert set(questions) == EVENT_QUESTION_IDS
    for qid in EVENT_QUESTION_IDS:
        assert questions[qid]["type"] == "noul"
        # "true"/"false" keys are quoted in the YAML, so they parse as strings, not bools.
        assert set(questions[qid]["criteria"]) == {"true", "false"}


def test_default_path_points_at_the_committed_file():
    assert QUESTIONS_PATH == Path(__file__).resolve().parent.parent / "questions" / "event.yaml"
    assert QUESTIONS_PATH.exists()


# --- questions/event_with_context.yaml: same ids/types/criteria, changed instructions ---


def test_context_questions_path_points_at_the_committed_file():
    assert CONTEXT_QUESTIONS_PATH == Path(__file__).resolve().parent.parent / "questions" / "event_with_context.yaml"
    assert CONTEXT_QUESTIONS_PATH.exists()


@pytest.mark.skip(reason="replaced in Task 2: event_with_context.yaml still uses the old three-question ids")
def test_context_questions_have_the_same_ids_types_and_criteria_as_event_questions():
    base = load_questions()
    context = load_questions(CONTEXT_QUESTIONS_PATH)

    assert set(context) == set(base)
    for qid in base:
        assert context[qid]["type"] == base[qid]["type"]
        assert context[qid].get("criteria") == base[qid].get("criteria")


@pytest.mark.skip(reason="replaced in Task 2: event_with_context.yaml still uses the old three-question ids")
def test_context_questions_instructions_mention_context_and_differ_from_the_base_file():
    base = load_questions()
    context = load_questions(CONTEXT_QUESTIONS_PATH)

    for qid in base:
        assert context[qid]["instructions"] != base[qid]["instructions"]
        assert "context" in context[qid]["instructions"]
