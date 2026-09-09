from __future__ import annotations

from types import SimpleNamespace

from harness_rag import supervisor
from harness_rag.prompts import PLANNER_SCHEMA, planner_prompt, reviewer_prompt, worker_prompt
from harness_rag.supervisor import _full_history, _rich_row


def test_full_history_keeps_all_cycles() -> None:
    history = [
        {
            "cycle": cycle,
            "hypothesis": f"h{cycle}",
            "decision": "REJECTED",
            "public_hard_pass_rate": 0.8,
            "blind_hard_pass_rate": 0.76,
        }
        for cycle in range(1, 16)
    ]

    visible = _full_history(history)

    assert len(visible) == 15
    assert visible[0]["cycle"] == 1
    assert visible[-1]["cycle"] == 15


def test_rich_row_preserves_plan_and_metric_memory() -> None:
    row = _rich_row(
        cycle=2,
        plan={
            "hypothesis": "change the matcher architecture",
            "change_name": "cycle-02-matcher-architecture",
            "plan_summary": "Try a different candidate-selection architecture.",
            "expected_effect": "Improve several wrong-product cases.",
            "expected_metric_gain": "+2 to +4 hard-pass cases",
            "lesson_from_history": "The previous local filter regressed safety.",
            "candidate_hypotheses": ["a", "b", "c"],
        },
        decision="REJECTED",
        reason="blind coverage regressed",
        public_rate=0.83,
        blind_rate=0.73,
        state={
            "baseline_public": {"hard_pass_rate": 0.80},
            "baseline_hidden": {"hard_pass_rate": 0.7666666667},
            "champion_public": {"hard_pass_rate": 0.80, "false_match_rate": 0.0},
            "champion_hidden": {"hard_pass_rate": 0.7666666667, "false_match_rate": 0.0},
            "index_builds_used": 0,
        },
    )

    assert row["plan_summary"]
    assert row["candidate_hypotheses"] == ["a", "b", "c"]
    assert row["public_delta_vs_baseline"] > 0
    assert row["blind_delta_vs_baseline"] < 0
    assert row["champion_blind_metrics_after_decision"]["false_match_rate"] == 0.0


def test_planner_contract_requires_alternatives_and_broad_mvp_freedom() -> None:
    required = set(PLANNER_SCHEMA["required"])
    assert "candidate_hypotheses" in required
    assert "lesson_from_history" in required
    assert "expected_metric_gain" in required

    prompt = planner_prompt(
        cycle=3,
        goal="Improve MVP accuracy.",
        research_context="Evidence only.",
        taxonomy="{}",
        history=[{"cycle": 1, "hypothesis": "old idea", "decision": "REJECTED"}],
        public_failures=[],
        public_metrics={"hard_pass_rate": 0.8},
        hidden_metrics={"hard_pass_rate": 0.7666666667},
        index_builds_used=0,
        max_index_builds=5,
        coverage_floor=0.93,
    )

    assert "any product implementation component" in prompt
    assert "at least 3 materially different candidate hypotheses" in prompt
    assert "COMPLETE HYPOTHESIS / METRIC HISTORY" in prompt
    assert "cycle-03-" in prompt
    assert "hardcode a test id" in prompt
    assert "NEVER assign public harness evaluation" in prompt
    assert "BLOCKED outcomes as inconclusive" in prompt


def test_worker_and_reviewer_leave_supervisor_metrics_to_supervisor() -> None:
    plan = {"change_name": "cycle-02-demo", "hypothesis": "demo"}
    worker = worker_prompt(goal="Improve MVP accuracy.", plan=plan)
    reviewer = reviewer_prompt(
        goal="Improve MVP accuracy.",
        plan=plan,
        worker_result={"status": "complete"},
        diff_text="",
        public_metrics={"hard_pass_rate": 0.8},
        public_failures=[],
    )

    assert "Do not return `blocked` merely because public/blind acceptance metrics are unavailable" in worker
    assert "outer supervisor, not the Implementer or Reviewer, owns the blind evaluation" in reviewer


def test_blocked_rejection_is_not_scientific_reject() -> None:
    assert supervisor._rejection_kind("Implementer blocked on missing supervisor metrics") == "BLOCKED"
    assert supervisor._rejection_kind("Tests failed after fixer") == "IMPLEMENTATION_FAILED"
    assert supervisor._rejection_kind("Gate-on public harness regressed") == "REJECTED"


def test_planner_may_create_only_one_untracked_cycle_openspec(monkeypatch) -> None:
    monkeypatch.setattr(
        supervisor.orchestrator,
        "changed_paths",
        lambda: {
            "openspec/changes/cycle-04-better-matcher/proposal.md",
            "openspec/changes/cycle-04-better-matcher/tasks.md",
        },
    )
    monkeypatch.setattr(
        supervisor.orchestrator,
        "git",
        lambda *args, **kwargs: SimpleNamespace(stdout=""),
    )
    assert supervisor._planner_created_one_new_cycle_change() is True

    monkeypatch.setattr(
        supervisor.orchestrator,
        "git",
        lambda *args, **kwargs: SimpleNamespace(stdout="openspec/changes/cycle-04-better-matcher/proposal.md\n"),
    )
    assert supervisor._planner_created_one_new_cycle_change() is False


def test_planner_cannot_rewrite_old_non_cycle_openspec(monkeypatch) -> None:
    monkeypatch.setattr(
        supervisor.orchestrator,
        "changed_paths",
        lambda: {"openspec/changes/add-rag-business-mapping/proposal.md"},
    )
    assert supervisor._planner_created_one_new_cycle_change() is False


def test_planner_may_stop_below_target_when_hypotheses_are_exhausted() -> None:
    prompt = supervisor._planner_prompt_with_exhaustion_stop(
        cycle=5,
        goal="Improve MVP accuracy.",
        research_context="Evidence only.",
        taxonomy="{}",
        history=[{"cycle": 1, "hypothesis": "old idea", "decision": "REJECTED"}],
        public_failures=[],
        public_metrics={"hard_pass_rate": 0.8},
        hidden_metrics={"hard_pass_rate": 0.7666666667},
        index_builds_used=1,
        max_index_builds=5,
        coverage_floor=0.93,
    )

    assert "STOPPING RULE" in prompt
    assert "MAY return `action=DONE` below the target" in prompt
    assert "Do not invent a weak" in prompt
    assert PLANNER_SCHEMA["properties"]["candidate_hypotheses"]["minItems"] == 0

    hidden = supervisor.orchestrator.Metrics.from_summary({"hard_pass_rate": 0.80})
    previous = supervisor._PLANNER_REQUESTED_DONE
    try:
        supervisor._PLANNER_REQUESTED_DONE = True
        assert supervisor._goal_met_or_planner_exhausted(hidden=hidden, coverage_floor=0.93) is True
    finally:
        supervisor._PLANNER_REQUESTED_DONE = previous
