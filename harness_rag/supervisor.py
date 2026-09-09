"""Compatibility entrypoint plus research-memory enrichment for the RAG harness."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import orchestrator


_ORIGINAL_ROW = orchestrator._row
_ORIGINAL_REJECT = orchestrator._reject
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
        "failure_stage",
        "review_decision",
        "public_hard_pass_rate",
        "blind_hard_pass_rate",
        "candidate_public_metrics",
        "candidate_blind_evaluated",
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


def _candidate_public_summary(state_dir: Path, cycle: int) -> dict[str, Any]:
    """Recover public metrics already produced before an early reject."""
    run_dir = state_dir / "runs" / f"{cycle:03d}"
    for name in ("public_after_fixer.json", "public_after_worker.json"):
        path = run_dir / name
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        summary = payload.get("summary")
        if isinstance(summary, dict):
            return dict(summary)
    return {}


def _rejection_kind(reason: str) -> str:
    """Do not teach the Planner that a protocol/implementation block disproved a hypothesis."""
    text = reason.lower()
    if "blocked" in text or "supervisor" in text and "unavailable" in text:
        return "BLOCKED"
    if "tests failed" in text or "did not pass tests" in text or "evaluation failed" in text:
        return "IMPLEMENTATION_FAILED"
    return "REJECTED"


def _reject_with_attempt_memory(**kwargs: Any) -> None:
    """Record attempted public metrics and distinguish scientific rejects from blocked runs."""
    state_dir = Path(kwargs["state_dir"])
    state = kwargs["state"]
    champion_commit = str(kwargs["champion_commit"])
    cycle = int(kwargs["cycle"])
    plan = dict(kwargs.get("plan") or {})
    reason = str(kwargs.get("reason") or "Candidate rejected.")
    champion_public = kwargs["champion_public"]
    champion_hidden = kwargs["champion_hidden"]
    review_decision = kwargs.get("review_decision")
    candidate_alias = kwargs.get("candidate_alias")

    attempted_public = _candidate_public_summary(state_dir, cycle)
    attempted_public_rate = float(
        attempted_public.get("hard_pass_rate", champion_public.hard_pass_rate)
    )
    decision = _rejection_kind(reason)
    failure_stage = (
        "REVIEW"
        if review_decision == "REJECT"
        else "PUBLIC_EVAL_OR_REVIEW"
        if attempted_public
        else "IMPLEMENTATION_OR_TEST"
    )

    orchestrator.rollback(champion_commit)
    row = orchestrator._row(
        cycle=cycle,
        plan=plan,
        decision=decision,
        reason=reason,
        public_rate=attempted_public_rate,
        blind_rate=champion_hidden.hard_pass_rate,
        state=state,
        review_decision=review_decision,
        candidate_alias=candidate_alias,
    )
    row["failure_stage"] = failure_stage
    row["candidate_public_metrics"] = attempted_public
    row["candidate_blind_evaluated"] = False
    orchestrator._record(state_dir, state, row)

    print(f"\n--- CYCLE {cycle} {decision} ---", flush=True)
    print(f"Hypothesis: {plan.get('hypothesis', '')}", flush=True)
    if attempted_public:
        print(
            "Public hard-pass: "
            f"{champion_public.hard_pass_rate:.3f} -> {attempted_public_rate:.3f}",
            flush=True,
        )
    else:
        print("Public hard-pass: not evaluated", flush=True)
    print("Blind: not evaluated; champion preserved", flush=True)
    print(f"Reason: {reason}", flush=True)


# For DONE, the Planner is allowed to return no remaining candidate hypotheses.
# IMPLEMENT still requires >=3 alternatives via the prompt contract and tests.
orchestrator.PLANNER_SCHEMA["properties"]["candidate_hypotheses"]["minItems"] = 0

# The orchestrator resolves these globals at runtime, so the wrapper can enrich
# memory and tighten the planning/stopping contract without duplicating the main loop.
orchestrator._row = _rich_row
orchestrator._compact_history = _full_history
orchestrator._reject = _reject_with_attempt_memory
orchestrator.planner_changes_are_scoped = _planner_created_one_new_cycle_change
orchestrator.planner_prompt = _planner_prompt_with_exhaustion_stop
orchestrator.run_codex = _run_codex_with_done_tracking
orchestrator.final_goal_met = _goal_met_or_planner_exhausted
orchestrator._write_final_report = _write_final_report_with_exhaustion_outcome


def main() -> int:
    return orchestrator.main()


if __name__ == "__main__":
    raise SystemExit(main())
