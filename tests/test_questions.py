from pathlib import Path

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
