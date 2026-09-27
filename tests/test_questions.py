from pathlib import Path

from jev_turbine.escalate import QUESTIONS_PATH as CONTEXT_QUESTIONS_PATH
from jev_turbine.triage import QUESTIONS_PATH, load_questions


def test_event_questions_load_with_the_three_expected_ids_and_types():
    questions = load_questions()

    assert set(questions) == {"cause", "safety_related", "needs_site_visit"}
    assert questions["cause"]["type"] == "choice"
    assert set(questions["cause"]["criteria"]) == {"fault", "planned", "external", "running"}
    assert questions["safety_related"]["type"] == "noul"
    assert questions["needs_site_visit"]["type"] == "noul"
    # "true"/"false" keys are quoted in the YAML, so they parse as strings, not bools.
    assert set(questions["needs_site_visit"]["criteria"]) == {"true", "false"}


def test_default_path_points_at_the_committed_file():
    assert QUESTIONS_PATH == Path(__file__).resolve().parent.parent / "questions" / "event.yaml"
    assert QUESTIONS_PATH.exists()


# --- questions/event_with_context.yaml: same ids/types/criteria, changed instructions ---


def test_context_questions_path_points_at_the_committed_file():
    assert CONTEXT_QUESTIONS_PATH == Path(__file__).resolve().parent.parent / "questions" / "event_with_context.yaml"
    assert CONTEXT_QUESTIONS_PATH.exists()


def test_context_questions_have_the_same_ids_types_and_criteria_as_event_questions():
    base = load_questions()
    context = load_questions(CONTEXT_QUESTIONS_PATH)

    assert set(context) == set(base)
    for qid in base:
        assert context[qid]["type"] == base[qid]["type"]
        assert context[qid].get("criteria") == base[qid].get("criteria")


def test_context_questions_instructions_mention_context_and_differ_from_the_base_file():
    base = load_questions()
    context = load_questions(CONTEXT_QUESTIONS_PATH)

    for qid in base:
        assert context[qid]["instructions"] != base[qid]["instructions"]
        assert "context" in context[qid]["instructions"]
