"""Compatibility entrypoint plus research-memory enrichment for the RAG harness."""

from __future__ import annotations

from typing import Any

from . import orchestrator


_ORIGINAL_ROW = orchestrator._row
_ORIGINAL_PLANNER_PROMPT = orchestrator.planner_prompt
_ORIGINAL_RUN_CODEX = orchestrator.run_codex
_ORIGINAL_FINAL_GOAL_MET = orchestrator.final_goal_met
_ORIGINAL_WRITE_FINAL_REPORT = orchestrator._write_final_report
_PLANNER_REQUESTED_DONE = False


def _rich_row(**kwargs: Any) -> dict[str, Any]:
    """Keep enough experiment context for later Planner cycles to learn from."""
    row = _ORIGINAL_ROW(**kwargs)
    plan = dict(kwargs.get("plan") or {})
    state = dict(kwargs.get("state") or {})

    row.update(
        {
            "plan_summary": plan.get("plan_summary", ""),
            "expected_effect": plan.get("expected_effect", ""),
            "expected_metric_gain": plan.get("expected_metric_gain", ""),
            "lesson_from_history": plan.get("lesson_from_history", ""),
            "candidate_hypotheses": list(plan.get("candidate_hypotheses") or []),
            "champion_public_metrics_after_decision": dict(state.get("champion_public") or {}),
            "champion_blind_metrics_after_decision": dict(state.get("champion_hidden") or {}),
        }
    )

    baseline_public = float((state.get("baseline_public") or {}).get("hard_pass_rate") or 0.0)
    baseline_blind = float((state.get("baseline_hidden") or {}).get("hard_pass_rate") or 0.0)
    row["public_delta_vs_baseline"] = float(row.get("public_hard_pass_rate") or 0.0) - baseline_public
    row["blind_delta_vs_baseline"] = float(row.get("blind_hard_pass_rate") or 0.0) - baseline_blind
    return row


def _full_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Expose every cycle (max 15), not only a short trailing window."""
    keys = (
        "cycle",
        "hypothesis",
        "change_name",
        "plan_summary",
        "expected_effect",
        "expected_metric_gain",
        "lesson_from_history",
        "candidate_hypotheses",
        "decision",
        "reason",
        "review_decision",
        "public_hard_pass_rate",
        "blind_hard_pass_rate",
        "public_delta_vs_baseline",
        "blind_delta_vs_baseline",
        "champion_public_metrics_after_decision",
        "champion_blind_metrics_after_decision",
        "index_builds_used",
        "candidate_collection_alias",
        "at",
    )
    return [{key: row.get(key) for key in keys if key in row} for row in history]


def _planner_created_one_new_cycle_change() -> bool:
    """Planner may create one fresh cycle OpenSpec, never rewrite an old one."""
    paths = orchestrator.changed_paths()
    if not paths:
        return False

    roots: set[str] = set()
    for path in paths:
        parts = path.split("/")
        if len(parts) < 4 or parts[0:2] != ["openspec", "changes"]:
            return False
        change_name = parts[2]
        if not change_name.startswith("cycle-"):
            return False
        roots.add(f"openspec/changes/{change_name}")

    if len(roots) != 1:
        return False

    root = next(iter(roots))
    # A previous accepted experiment is tracked by Git. Planning must never edit it.
    tracked = orchestrator.git("ls-files", root, check=False).stdout.strip()
    return not tracked


def _planner_prompt_with_exhaustion_stop(**kwargs: Any) -> str:
    """Keep the 93% target, but do not force low-value cycles when ideas are exhausted."""
    prompt = _ORIGINAL_PLANNER_PROMPT(**kwargs)
    coverage_floor = float(kwargs.get("coverage_floor") or 0.0)
    return (
        prompt
        + f"""

STOPPING RULE — THIS OVERRIDES ANY EARLIER DONE RESTRICTION IN THIS PROMPT
- {coverage_floor:.2%} blind coverage is the success target, not a requirement to consume all 15 cycles.
- You MAY return `action=DONE` below the target when, after reviewing the repository, complete experiment history, current failures, research context, and the main plausible solution families, you cannot identify a credible new experiment with positive expected metric gain or information gain.
- Do not invent a weak, repetitive, benchmark-specific, or low-value hypothesis merely to spend another cycle.
- If you return DONE because the search space is exhausted, explain the evidence in `why_now` and `lesson_from_history`. `candidate_hypotheses` may be empty or may list directions you considered and rejected.
- If you return IMPLEMENT, still consider at least 3 materially different candidate hypotheses before choosing one.
"""
    )


def _run_codex_with_done_tracking(**kwargs: Any) -> dict[str, Any]:
    """Remember when the Planner intentionally asks the outer loop to stop."""
    global _PLANNER_REQUESTED_DONE
    payload = _ORIGINAL_RUN_CODEX(**kwargs)
    if kwargs.get("role") == "planner":
        _PLANNER_REQUESTED_DONE = payload.get("action") == "DONE"
    return payload


def _goal_met_or_planner_exhausted(*, hidden: Any, coverage_floor: float) -> bool:
    """Let an evidence-backed Planner DONE terminate cleanly even below the success target."""
    if _PLANNER_REQUESTED_DONE:
        return True
    return _ORIGINAL_FINAL_GOAL_MET(hidden=hidden, coverage_floor=coverage_floor)


def _write_final_report_with_exhaustion_outcome(**kwargs: Any) -> None:
    """Distinguish target success from a graceful no-more-useful-hypotheses stop."""
    outcome = str(kwargs.get("outcome") or "")
    state = dict(kwargs.get("state") or {})
    config = dict(kwargs.get("config") or {})
    if outcome == "DONE" and _PLANNER_REQUESTED_DONE:
        hidden = orchestrator.Metrics.from_summary(dict(state.get("champion_hidden") or {}))
        floor = float(config.get("coverage_floor") or 0.0)
        if not _ORIGINAL_FINAL_GOAL_MET(hidden=hidden, coverage_floor=floor):
            kwargs["outcome"] = "EXHAUSTED"
    _ORIGINAL_WRITE_FINAL_REPORT(**kwargs)


# For DONE, the Planner is allowed to return no remaining candidate hypotheses.
# IMPLEMENT still requires >=3 alternatives via the prompt contract and tests.
orchestrator.PLANNER_SCHEMA["properties"]["candidate_hypotheses"]["minItems"] = 0

# The orchestrator resolves these globals at runtime, so the wrapper can enrich
# memory and tighten the planning/stopping contract without duplicating the main loop.
orchestrator._row = _rich_row
orchestrator._compact_history = _full_history
orchestrator.planner_changes_are_scoped = _planner_created_one_new_cycle_change
orchestrator.planner_prompt = _planner_prompt_with_exhaustion_stop
orchestrator.run_codex = _run_codex_with_done_tracking
orchestrator.final_goal_met = _goal_met_or_planner_exhausted
orchestrator._write_final_report = _write_final_report_with_exhaustion_outcome


def main() -> int:
    return orchestrator.main()


if __name__ == "__main__":
    raise SystemExit(main())
