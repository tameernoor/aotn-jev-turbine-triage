from pathlib import Path

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
