from __future__ import annotations

from types import SimpleNamespace

from harness_rag import supervisor
from harness_rag.prompts import PLANNER_SCHEMA, planner_prompt
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
