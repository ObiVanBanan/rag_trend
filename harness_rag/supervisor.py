"""Compatibility entrypoint plus research-memory enrichment for the RAG harness."""

from __future__ import annotations

from typing import Any

from . import orchestrator


_ORIGINAL_ROW = orchestrator._row


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


# The orchestrator resolves these globals at runtime, so the wrapper can enrich
# memory and tighten the planning contract without duplicating the main loop.
orchestrator._row = _rich_row
orchestrator._compact_history = _full_history
orchestrator.planner_changes_are_scoped = _planner_created_one_new_cycle_change


def main() -> int:
    return orchestrator.main()


if __name__ == "__main__":
    raise SystemExit(main())
