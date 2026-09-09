from __future__ import annotations

from harness_rag.policy import Metrics, accept_candidate, final_goal_met


def _metrics(**overrides: float) -> Metrics:
    values = {
        "hard_pass_rate": 0.92,
        "core_pass_rate": 0.90,
        "negative_pass_rate": 1.0,
        "wrong_not_found_rate": 0.04,
        "false_match_rate": 0.0,
        "unknown_answer_rate": 0.02,
        "human_reject_rate": 0.0,
        "known_positive_hit_rate": 0.80,
    }
    values.update(overrides)
    return Metrics(**values)


def test_accepts_monotonic_blind_improvement() -> None:
    champion = _metrics()
    candidate = _metrics(hard_pass_rate=0.96, core_pass_rate=0.95)

    accepted, reason = accept_candidate(
        candidate_public=candidate,
        candidate_hidden=candidate,
        champion_public=champion,
        champion_hidden=champion,
    )

    assert accepted is True
    assert "improves" in reason


def test_rejects_blind_coverage_regression_even_when_public_improves() -> None:
    champion_public = _metrics()
    champion_hidden = _metrics(hard_pass_rate=0.96)
    candidate_public = _metrics(hard_pass_rate=1.0, core_pass_rate=1.0)
    candidate_hidden = _metrics(hard_pass_rate=0.92)

    accepted, reason = accept_candidate(
        candidate_public=candidate_public,
        candidate_hidden=candidate_hidden,
        champion_public=champion_public,
        champion_hidden=champion_hidden,
    )

    assert accepted is False
    assert reason == "blind coverage regressed"


def test_rejects_public_coverage_regression_even_when_blind_improves() -> None:
    champion_public = _metrics(hard_pass_rate=0.96)
    champion_hidden = _metrics(hard_pass_rate=0.92)
    candidate_public = _metrics(hard_pass_rate=0.92)
    candidate_hidden = _metrics(hard_pass_rate=0.96)

    accepted, reason = accept_candidate(
        candidate_public=candidate_public,
        candidate_hidden=candidate_hidden,
        champion_public=champion_public,
        champion_hidden=champion_hidden,
    )

    assert accepted is False
    assert reason == "public coverage regressed"


def test_rejects_new_false_match() -> None:
    champion = _metrics()
    candidate = _metrics(hard_pass_rate=0.96, false_match_rate=0.01)

    accepted, reason = accept_candidate(
        candidate_public=candidate,
        candidate_hidden=candidate,
        champion_public=champion,
        champion_hidden=champion,
    )

    assert accepted is False
    assert "safety" in reason


def test_equal_blind_can_accept_public_improvement() -> None:
    champion_public = _metrics(core_pass_rate=0.88)
    candidate_public = _metrics(core_pass_rate=0.91)
    hidden = _metrics(hard_pass_rate=0.96)

    accepted, _ = accept_candidate(
        candidate_public=candidate_public,
        candidate_hidden=hidden,
        champion_public=champion_public,
        champion_hidden=hidden,
    )

    assert accepted is True


def test_final_goal_requires_93_percent_and_no_false_or_human_reject() -> None:
    assert final_goal_met(hidden=_metrics(hard_pass_rate=0.93, wrong_not_found_rate=0.0), coverage_floor=0.93)
    assert not final_goal_met(hidden=_metrics(hard_pass_rate=0.929, wrong_not_found_rate=0.0), coverage_floor=0.93)
    assert not final_goal_met(hidden=_metrics(hard_pass_rate=0.95, false_match_rate=0.01), coverage_floor=0.93)
    assert not final_goal_met(hidden=_metrics(hard_pass_rate=0.95, human_reject_rate=0.01), coverage_floor=0.93)


def test_optional_public_final_floor_can_be_enforced() -> None:
    hidden = _metrics(hard_pass_rate=0.96, wrong_not_found_rate=0.0)
    assert final_goal_met(public=_metrics(hard_pass_rate=0.93), hidden=hidden, coverage_floor=0.93)
    assert not final_goal_met(public=_metrics(hard_pass_rate=0.92), hidden=hidden, coverage_floor=0.93)
