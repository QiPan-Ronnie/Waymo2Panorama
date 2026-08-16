from __future__ import annotations

from agent.db181_multids.generated_batch_result import require_successful_batch


def test_batch_guard_accepts_only_completed_error_free_cases() -> None:
    result = require_successful_batch(
        {
            "OUT": {
                "status": "db89_completed",
                "cases": [
                    {"case": "a000", "n_objects_composited": 0},
                    {"case": "a039", "n_objects_composited": 0},
                ],
            }
        },
        expected_cases=2,
    )
    assert len(result["cases"]) == 2


def test_batch_guard_promotes_swallowed_case_error_to_failure() -> None:
    try:
        require_successful_batch(
            {
                "OUT": {
                    "status": "db89_completed",
                    "cases": [{"case": "a000", "error": "KeyError: camera"}],
                }
            },
            expected_cases=1,
        )
    except RuntimeError as error:
        assert "a000" in str(error)
        assert "KeyError: camera" in str(error)
    else:
        raise AssertionError("swallowed DB89 case error must fail the runner")


def test_batch_guard_rejects_wrong_case_count() -> None:
    try:
        require_successful_batch(
            {"OUT": {"status": "db89_completed", "cases": []}},
            expected_cases=1,
        )
    except RuntimeError as error:
        assert "expected 1 cases, got 0" in str(error)
    else:
        raise AssertionError("missing cases must fail the runner")
