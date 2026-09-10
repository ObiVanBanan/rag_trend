from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from . import v2 as core
from .policy import Metrics, final_goal_met
from .runtime import branch, clean, ensure_outside_repo, head, write_json


EXECUTION_COUNTERS_VERSION = 1


def _experiment_id(row: dict[str, Any], active: dict[str, Any]) -> str:
    existing = str(active.get("experiment_id") or row.get("experiment_id") or "").strip()
    if existing:
        return existing
    plan = dict(active.get("plan") or {})
    declared = str(plan.get("experiment_id") or "").strip()
    if declared:
        return declared
    family = str(row.get("family") or plan.get("hypothesis_family") or "experiment").strip().lower()
    hypothesis = str(row.get("hypothesis") or plan.get("hypothesis") or plan.get("research_question") or "").strip()
    digest = hashlib.sha256(f"{family}\n{hypothesis}".encode("utf-8")).hexdigest()[:10]
    safe_family = "".join(ch if ch.isalnum() else "-" for ch in family).strip("-") or "experiment"
    return f"{safe_family}-{digest}"


_ORIGINAL_PERSIST_ACTIVE = core._persist_active


def _persist_active_with_ids(state: dict[str, Any], state_path: Path, active: dict[str, Any]) -> None:
    plan = dict(active.get("plan") or {})
    if plan and not active.get("experiment_id"):
        active["experiment_id"] = _experiment_id(
            {
                "family": plan.get("hypothesis_family") or "experiment",
                "hypothesis": plan.get("hypothesis") or plan.get("research_question") or "",
            },
            active,
        )
    _ORIGINAL_PERSIST_ACTIVE(state, state_path, active)


def _looks_like_pre_runner_campaign(state: dict[str, Any]) -> bool:
    """Detect an old 1-17 campaign that already carries STATE_VERSION=3.

    The first v2 implementation bumped the state version before attempt/scientific
    accounting existed. Such a state therefore bypasses the numeric-version
    migration even though its history is still legacy-shaped. It is safe to
    rehome only when no v2 campaign identity/calls/active attempt exist and none
    of the history rows already carries the new attempt/scientific fields.
    """
    history = [row for row in state.get("history") or [] if isinstance(row, dict)]
    if not history:
        return False
    if state.get("campaign_id") or state.get("active"):
        return False
    usage = dict(state.get("usage") or {})
    if int(usage.get("agent_calls") or 0) > 0:
        return False
    if int(state.get("execution_counters_version") or 0) >= EXECUTION_COUNTERS_VERSION:
        # A previous buggy runner invocation may have stamped counters onto the
        # legacy history without actually starting a campaign. Treat that exact
        # shape as legacy too when there are still no v2 row identifiers.
        pass
    return all(
        row.get("attempt_id") is None
        and row.get("experiment_id") is None
        and row.get("scientific_iteration") is None
        for row in history
    )


def _rehome_pre_runner_campaign(state: dict[str, Any]) -> dict[str, Any]:
    migrated = core._new_state_from_legacy(state)
    migrated["attempts_started"] = 0
    migrated["scientific_iterations"] = 0
    migrated["execution_counters_version"] = EXECUTION_COUNTERS_VERSION
    migrated["push_pending"] = bool(state.get("push_pending", False))
    return migrated


def _ensure_execution_counters(state: dict[str, Any]) -> None:
    history = [row for row in state.get("history") or [] if isinstance(row, dict)]
    max_attempt = max(
        [int(state.get("cycle") or 0)]
        + [int(row.get("attempt_id") or row.get("cycle") or 0) for row in history],
    )
    if "attempts_started" not in state:
        state["attempts_started"] = max_attempt
    if "scientific_iterations" not in state:
        state["scientific_iterations"] = sum(1 for row in history if bool(row.get("scientifically_evaluated")))
    state["execution_counters_version"] = EXECUTION_COUNTERS_VERSION


def _stamp_completion(state: dict[str, Any], row: dict[str, Any], active: dict[str, Any]) -> dict[str, Any]:
    _ensure_execution_counters(state)
    attempt_id = int(active.get("attempt_id") or row.get("attempt_id") or row.get("cycle") or state.get("attempts_started") or 0)
    state["attempts_started"] = max(int(state.get("attempts_started") or 0), attempt_id)
    row["attempt_id"] = attempt_id
    row["experiment_id"] = _experiment_id(row, active)
    if bool(row.get("scientifically_evaluated")):
        state["scientific_iterations"] = int(state.get("scientific_iterations") or 0) + 1
        row["scientific_iteration"] = int(state["scientific_iterations"])
    else:
        row["scientific_iteration"] = None
    return row


def _record_cycle_scientific(*, state_dir: Path, state: dict[str, Any], row: dict[str, Any]) -> None:
    active = dict(state.get("active") or {})
    row = _stamp_completion(state, row, active)
    state.setdefault("history", []).append(row)
    state["cycle"] = int(row.get("attempt_id") or row["cycle"])
    state["active"] = None
    if row.get("action") == "IMPLEMENT":
        core._update_ledger(state, row)
    write_json(state_dir / "state.json", state)
    with (state_dir / "history_v2.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    core._append_event(
        state_dir,
        "ATTEMPT_COMPLETED",
        attempt_id=row.get("attempt_id"),
        experiment_id=row.get("experiment_id"),
        scientific_iteration=row.get("scientific_iteration"),
        decision=row.get("decision"),
        error_code=row.get("error_code"),
    )


def _start_attempt(state: dict[str, Any], state_path: Path, state_dir: Path) -> dict[str, Any]:
    _ensure_execution_counters(state)
    attempt_id = int(state.get("attempts_started") or 0) + 1
    state["attempts_started"] = attempt_id
    active = {
        "cycle": attempt_id,  # compatibility with the existing v2 stage engine and run directory layout
        "attempt_id": attempt_id,
        "stage": "PLANNER",
        "started_at": core._now(),
    }
    state["active"] = active
    write_json(state_path, state)
    core._append_event(state_dir, "ATTEMPT_STARTED", attempt_id=attempt_id)
    return active


def _start_new_campaign(state: dict[str, Any]) -> dict[str, Any]:
    fresh = core._start_new_campaign(state)
    fresh["attempts_started"] = 0
    fresh["scientific_iterations"] = 0
    fresh["execution_counters_version"] = EXECUTION_COUNTERS_VERSION
    return fresh


def _write_final_report(
    *,
    state_dir: Path,
    state: dict[str, Any],
    config: dict[str, Any],
    outcome: str,
    final_summary: dict[str, Any] | None,
) -> None:
    public = dict(state.get("champion_public") or {})
    hidden = dict(state.get("champion_hidden") or {})
    usage = dict(state.get("usage") or {})
    lines = [
        "# RAG Harness v2 Final Report",
        "",
        f"- Outcome: **{outcome}**",
        f"- Attempts started: **{state.get('attempts_started', 0)}**",
        f"- Scientific iterations: **{state.get('scientific_iterations', 0)}** / {config['max_cycles']}",
        f"- Champion commit: `{state.get('champion_commit', '')}`",
        f"- Public hard-pass: **{public.get('hard_pass_rate')}**",
        f"- Hidden-validation hard-pass: **{hidden.get('hard_pass_rate')}**",
        f"- Sealed final hard-pass: **{(final_summary or {}).get('hard_pass_rate', 'not run')}**",
        f"- Agent calls: **{usage.get('agent_calls', 0)}** / {config.get('max_agent_calls', 18)}",
        f"- Planner / Implementer / Reviewer / Fixer / Research: **{usage.get('planner_calls', 0)} / {usage.get('implementer_calls', 0)} / {usage.get('reviewer_calls', 0)} / {usage.get('fixer_calls', 0)} / {usage.get('research_calls', 0)}**",
        f"- Reported Codex tokens (when available): **{usage.get('reported_tokens', 0)}**",
        "",
        "## Campaign history",
        "",
    ]
    for row in state.get("history") or []:
        science = row.get("scientific_iteration")
        science_text = f"science={science}" if science is not None else "science=not-consumed"
        lines.append(
            f"- Attempt {row.get('attempt_id', row.get('cycle'))}: **{row.get('decision')}** "
            f"[{row.get('family')}] experiment=`{row.get('experiment_id', '')}` {science_text} — "
            f"{row.get('hypothesis', '')} (public={row.get('public_hard_pass_rate')}, "
            f"hidden={row.get('hidden_validation_hard_pass_rate')}, code={row.get('error_code')})"
        )
    (state_dir / "FINAL_REPORT_V2.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    # Reuse the proven stage engine, but replace completion accounting so the
    # scientific budget is independent from orchestration attempts.
    core._record_cycle = _record_cycle_scientific
    core._persist_active = _persist_active_with_ids

    args = core._parser().parse_args()
    config = core._read_json(core.CONFIG_PATH)
    core._apply_overrides(config, args)
    max_scientific_iterations = int(config["max_cycles"])
    if not 1 <= max_scientific_iterations <= 7:
        raise SystemExit("Harness v2 max scientific iterations must be in 1..7")
    if args.fresh and args.resume:
        raise SystemExit("--fresh and --resume are mutually exclusive")
    if args.new_campaign and args.resume:
        raise SystemExit("--new-campaign and --resume are mutually exclusive")
    if not clean() and not args.resume:
        raise SystemExit("Working tree must be clean before a new v2 attempt. Use --resume only for a preserved active candidate.")
    current_branch = branch()
    if current_branch in {"main", "master"}:
        raise SystemExit("Refusing autonomous edits on main/master")

    holdout = Path(args.holdout).expanduser().resolve()
    final_holdout = Path(args.final_holdout).expanduser().resolve() if args.final_holdout else None
    state_dir = core._state_dir(args, current_branch)
    ensure_outside_repo(state_dir, "Harness state directory")
    if args.fresh and state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "state.json"

    if state_path.exists():
        raw = core._read_json(state_path)
        if int(raw.get("version", 0)) < core.STATE_VERSION:
            write_json(state_dir / "state.v1-backup.json", raw)
            state = core._new_state_from_legacy(raw)
            core._append_event(state_dir, "STATE_MIGRATED", from_version=raw.get("version"), legacy_cycles=len(raw.get("history") or []))
        elif _looks_like_pre_runner_campaign(raw):
            write_json(state_dir / "state.pre-v2-runner-backup.json", raw)
            state = _rehome_pre_runner_campaign(raw)
            core._append_event(
                state_dir,
                "LEGACY_CAMPAIGN_REHOMED",
                legacy_cycles=len(raw.get("history") or []),
            )
        else:
            state = raw
    else:
        state = {
            "version": core.STATE_VERSION,
            "champion_commit": head(),
            "champion_collection_alias": os.environ.get("QDRANT_COLLECTION_ALIAS") or None,
            "cycle": 0,
            "index_builds_used": 0,
            "index_history": [],
            "history": [],
            "legacy_memory": [],
            "hypothesis_ledger": {},
            "research_memory": [],
            "usage": core._usage_defaults(),
            "active": None,
            "final_holdout_consumed": False,
        }
    _ensure_execution_counters(state)
    write_json(state_path, state)

    core._adopt_harness_only_head(state, state_path)
    if args.new_campaign:
        if state.get("active"):
            raise SystemExit("Cannot start a new campaign while an active attempt exists; resume or resolve it first.")
        state = _start_new_campaign(state)
        write_json(state_path, state)
        core._append_event(state_dir, "NEW_CAMPAIGN_STARTED", campaign_id=state["campaign_id"])

    try:
        report = core._preflight(config=config, state=state, holdout=holdout, final_holdout=final_holdout)
        write_json(state_dir / "preflight.json", {"at": core._now(), **report})
        core._append_event(state_dir, "PREFLIGHT_OK", qdrant=report["qdrant"])
    except Exception as exc:
        write_json(
            state_dir / "preflight.json",
            {"at": core._now(), "ok": False, "error_code": core._error_code(exc), "reason": core._short_reason(exc)},
        )
        core._append_event(state_dir, "PREFLIGHT_FAILED", error_code=core._error_code(exc), reason=core._short_reason(exc))
        print("\n=== PREFLIGHT_FAILED ===")
        print(core._short_reason(exc))
        print("No LLM call, attempt, or scientific iteration was consumed.")
        return core.TEMP_FAILURE_EXIT

    if "champion_public" not in state or not state.get("champion_public"):
        print("\n=== V2 BASELINE ===")
        try:
            baseline = core._baseline_state(config, holdout, state_dir)
        except Exception as exc:
            print(f"Baseline failed: {core._short_reason(exc)}")
            return core.TEMP_FAILURE_EXIT if core._error_code(exc).startswith("INFRA_") else 2
        state.update(baseline)
        _ensure_execution_counters(state)
        write_json(state_path, state)

    if args.resume:
        if not state.get("active"):
            raise SystemExit("No active v2 attempt to resume.")
        active = dict(state["active"])
        print(f"\n=== RESUME V2 ATTEMPT {active.get('attempt_id', active.get('cycle'))} | stage={active.get('stage')} ===")
        active.pop("paused_reason", None)
        active.pop("paused_error_code", None)
        state["active"] = active
        write_json(state_path, state)
        try:
            result = core._execute_active(args=args, config=config, state_dir=state_dir, state=state, holdout=holdout)
        except core.PauseRun as exc:
            return core._pause_external(state_dir=state_dir, state=state, code=exc.code, reason=exc.reason)
        if result == core.TEMP_FAILURE_EXIT:
            return result
        state = core._read_json(state_path)
        _ensure_execution_counters(state)
    elif state.get("active"):
        raise SystemExit("An interrupted v2 attempt exists. Rerun with --resume; completed stages will not be repeated.")

    while int(state.get("scientific_iterations", 0)) < max_scientific_iterations:
        if core._remaining_budgets(config, state)["agent_calls_remaining"] <= 0:
            print("Agent-call budget exhausted; stopping campaign without inventing more work.")
            break
        active = _start_attempt(state, state_path, state_dir)
        attempt_id = int(active["attempt_id"])
        print(
            f"\n######## HARNESS V2 ATTEMPT {attempt_id} | "
            f"SCIENTIFIC {int(state.get('scientific_iterations', 0))}/{max_scientific_iterations} ########"
        )
        try:
            result = core._execute_active(args=args, config=config, state_dir=state_dir, state=state, holdout=holdout)
        except core.PauseRun as exc:
            return core._pause_external(state_dir=state_dir, state=state, code=exc.code, reason=exc.reason)
        if result == core.TEMP_FAILURE_EXIT:
            return result
        state = core._read_json(state_path)
        _ensure_execution_counters(state)
        if result == 0 and (state.get("history") or []) and state["history"][-1].get("decision") == "DONE":
            break
        public = Metrics.from_summary(dict(state["champion_public"]))
        hidden = Metrics.from_summary(dict(state["champion_hidden"]))
        if final_goal_met(hidden=hidden, public=public, coverage_floor=float(config["coverage_floor"])):
            break

    final_summary = None
    try:
        final_summary = core._maybe_run_sealed_final(
            state_dir=state_dir,
            state=state,
            config=config,
            final_holdout=final_holdout,
        )
    except Exception as exc:
        if core._error_code(exc).startswith("INFRA_"):
            print(f"Sealed final paused by infrastructure: {core._short_reason(exc)}")
        else:
            raise

    public = Metrics.from_summary(dict(state["champion_public"]))
    hidden = Metrics.from_summary(dict(state["champion_hidden"]))
    goal_met = final_goal_met(hidden=hidden, public=public, coverage_floor=float(config["coverage_floor"]))
    if goal_met:
        outcome = "GOAL_MET"
    elif int(state.get("scientific_iterations", 0)) >= max_scientific_iterations:
        outcome = "MAX_SCIENTIFIC_ITERATIONS"
    else:
        outcome = "STOPPED"
    _write_final_report(
        state_dir=state_dir,
        state=state,
        config=config,
        outcome=outcome,
        final_summary=final_summary,
    )
    return 0 if goal_met else 2


if __name__ == "__main__":
    raise SystemExit(main())
