from __future__ import annotations

from harness_rag.diagnostic_runner import (
    _accept_candidate,
    _metric_rows,
    _public_precheck,
    _safety_regressions,
)
from harness_rag.policy import Metrics


def _metrics(**overrides: float) -> Metrics:
    payload = {
        "hard_pass_rate": 0.8333333333,
        "core_pass_rate": 0.8,
        "negative_pass_rate": 1.0,
        "wrong_not_found_rate": 0.0,
        "false_match_rate": 0.0,
        "unknown_answer_rate": 0.0,
        "human_reject_rate": 0.0,
        "known_positive_hit_rate": 0.4,
    }
    payload.update(overrides)
    return Metrics.from_summary(payload)


def test_public_precheck_names_exact_safety_regression() -> None:
    champion = _metrics()
    candidate = _metrics(false_match_rate=1 / 30)

    ok, reason = _public_precheck(candidate, champion)

    assert ok is False
    assert "public safety regression" in reason
    assert "false_match_rate" in reason
    assert "0.000000 -> 0.033333" in reason
    assert "delta +0.033333" in reason


def test_public_precheck_names_coverage_delta() -> None:
    champion = _metrics()
    candidate = _metrics(hard_pass_rate=0.8)

    ok, reason = _public_precheck(candidate, champion)

    assert ok is False
    assert "hard_pass_rate" in reason
    assert "0.833333 -> 0.800000" in reason
    assert "delta -0.033333" in reason


def test_hidden_safety_rejection_is_refined_without_changing_policy() -> None:
    champion_public = _metrics()
    champion_hidden = _metrics(hard_pass_rate=0.8666666667)
    candidate_public = _metrics(hard_pass_rate=0.8666666667)
    candidate_hidden = _metrics(hard_pass_rate=0.8666666667, wrong_not_found_rate=1 / 30)

    accepted, reason = _accept_candidate(
        candidate_public=candidate_public,
        candidate_hidden=candidate_hidden,
        champion_public=champion_public,
        champion_hidden=champion_hidden,
    )

    assert accepted is False
    assert "hidden validation safety regression" in reason
    assert "wrong_not_found_rate" in reason


def test_flat_safe_candidate_reaches_cheap_validator() -> None:
    champion_public = _metrics()
    champion_hidden = _metrics(hard_pass_rate=0.8666666667)

    accepted, reason = _accept_candidate(
        candidate_public=champion_public,
        candidate_hidden=champion_hidden,
        champion_public=champion_public,
        champion_hidden=champion_hidden,
    )

    assert accepted is True
    assert "guardrails held flat" in reason
    assert "cheap validator" in reason


def test_secondary_benchmark_regression_does_not_masquerade_as_flat() -> None:
    champion_public = _metrics()
    champion_hidden = _metrics(hard_pass_rate=0.8666666667)
    candidate_public = _metrics(unknown_answer_rate=0.1)

    accepted, reason = _accept_candidate(
        candidate_public=candidate_public,
        candidate_hidden=champion_hidden,
        champion_public=champion_public,
        champion_hidden=champion_hidden,
    )

    assert accepted is False
    assert reason == "public secondary benchmark metrics regressed"


def test_safety_regression_helper_reports_all_regressed_metrics() -> None:
    champion = _metrics()
    candidate = _metrics(false_match_rate=0.1, human_reject_rate=0.2)

    regressions = _safety_regressions(candidate, champion)

    assert [row[0] for row in regressions] == ["false_match_rate", "human_reject_rate"]


def test_metric_rows_include_champion_candidate_and_delta() -> None:
    champion = {"hard_pass_rate": 0.8, "false_match_rate": 0.0}
    candidate = {"hard_pass_rate": 0.8333333333, "false_match_rate": 0.0333333333}

    rows = {name: (before, after, delta) for name, before, after, delta in _metric_rows(candidate, champion)}

    assert rows["hard_pass_rate"] == (0.8, 0.8333333333, 0.03333333329999999)
    assert rows["false_match_rate"] == (0.0, 0.0333333333, 0.0333333333)
