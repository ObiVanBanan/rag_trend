from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .hook import check_dataset_outside_repo, run_eval, run_hidden_eval
from .policy import Metrics, accept_candidate, final_goal_met
from .prompts import (
    PLANNER_SCHEMA,
    REVIEW_SCHEMA,
    WORKER_SCHEMA,
    fixer_prompt,
    planner_prompt,
    reviewer_prompt,
    worker_prompt,
)
from .runtime import (
    HarnessError,
    ROOT,
    branch,
    changed_paths,
    clean,
    collection_env,
    ensure_outside_repo,
    git,
    head,
    planner_changes_are_scoped,
    protected_changes,
    rebuild_index,
    rollback,
    run_codex,
    run_tests,
    write_json,
)


HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
GOAL_PATH = HERE / "GOAL.md"
RESEARCH_PATH = HERE / "RESEARCH_CONTEXT.md"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _visible_hidden(summary: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "hard_gate_cases",
        "hard_pass_rate",
        "false_match_rate",
        "human_reject_rate",
        "wrong_not_found_rate",
        "unknown_answer_rate",
    )
    return {key: summary.get(key) for key in keys}


def _compact_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "cycle": row.get("cycle"),
            "hypothesis": row.get("hypothesis"),
            "decision": row.get("decision"),
            "reason": row.get("reason"),
            "public_hard_pass_rate": row.get("public_hard_pass_rate"),
            "blind_hard_pass_rate": row.get("blind_hard_pass_rate"),
            "index_builds_used": row.get("index_builds_used"),
        }
        for row in history[-8:]
    ]


def _record(state_dir: Path, state: dict[str, Any], row: dict[str, Any]) -> None:
    state.setdefault("history", []).append(row)
    write_json(state_dir / "state.json", state)
    with (state_dir / "history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _row(
    *,
    cycle: int,
    plan: dict[str, Any],
    decision: str,
    reason: str,
    public_rate: float,
    blind_rate: float,
    state: dict[str, Any],
    review_decision: str | None = None,
    candidate_alias: str | None = None,
) -> dict[str, Any]:
    return {
        "cycle": cycle,
        "hypothesis": plan.get("hypothesis", ""),
        "change_name": plan.get("change_name", ""),
        "decision": decision,
        "reason": reason,
        "public_hard_pass_rate": public_rate,
        "blind_hard_pass_rate": blind_rate,
        "index_builds_used": int(state.get("index_builds_used", 0)),
        "review_decision": review_decision,
        "candidate_collection_alias": candidate_alias,
        "at": _now(),
    }


def _reject(
    *,
    state_dir: Path,
    state: dict[str, Any],
    champion_commit: str,
    cycle: int,
    plan: dict[str, Any],
    reason: str,
    champion_public: Metrics,
    champion_hidden: Metrics,
    review_decision: str | None = None,
    candidate_alias: str | None = None,
) -> None:
    rollback(champion_commit)
    _record(
        state_dir,
        state,
        _row(
            cycle=cycle,
            plan=plan,
            decision="REJECTED",
            reason=reason,
            public_rate=champion_public.hard_pass_rate,
            blind_rate=champion_hidden.hard_pass_rate,
            state=state,
            review_decision=review_decision,
            candidate_alias=candidate_alias,
        ),
    )


def _baseline(
    *,
    config: dict[str, Any],
    holdout: Path,
    state_dir: Path,
) -> dict[str, Any]:
    eval_dir = state_dir / "eval"
    public = run_eval(
        dataset=ROOT / str(config["public_dataset"]),
        output_dir=eval_dir,
        tag="baseline_public",
    )
    hidden = run_hidden_eval(
        dataset=holdout,
        output_dir=eval_dir,
        tag="baseline_hidden",
    )
    current = head()
    state = {
        "version": 2,
        "started_at": _now(),
        "branch": branch(),
        "baseline_commit": current,
        "champion_commit": current,
        "champion_collection_alias": os.environ.get("QDRANT_COLLECTION_ALIAS") or None,
        "cycle": 0,
        "index_builds_used": 0,
        "index_history": [],
        "baseline_public": public["summary"],
        "baseline_hidden": hidden["summary"],
        "champion_public": public["summary"],
        "champion_hidden": hidden["summary"],
        "champion_public_failures": public["failures"],
        "history": [],
    }
    write_json(state_dir / "state.json", state)
    return state


def _write_final_report(
    *,
    state_dir: Path,
    state: dict[str, Any],
    config: dict[str, Any],
    outcome: str,
) -> None:
    public = state.get("champion_public") or {}
    hidden = state.get("champion_hidden") or {}
    lines = [
        "# RAG Harness Final Report",
        "",
        f"- Outcome: **{outcome}**",
        f"- Cycles completed: **{state.get('cycle', 0)}** / {config['max_cycles']}",
        f"- Index rebuilds: **{state.get('index_builds_used', 0)}** / {config['max_index_builds']}",
        f"- Champion commit: `{state.get('champion_commit', '')}`",
        f"- Champion Qdrant collection: `{state.get('champion_collection_alias') or 'default from environment/settings'}`",
        f"- Public hard-pass rate: **{public.get('hard_pass_rate')}**",
        f"- Blind hard-pass rate: **{hidden.get('hard_pass_rate')}**",
        f"- Blind success floor: **{float(config['coverage_floor']):.2%}**",
        "",
        "## Hypotheses",
        "",
    ]
    for row in state.get("history", []):
        lines.append(
            f"- Cycle {row.get('cycle')}: **{row.get('decision')}** — "
            f"{row.get('hypothesis', '')} "
            f"(public={row.get('public_hard_pass_rate')}, blind={row.get('blind_hard_pass_rate')})"
        )
    lines += [
        "",
        "If the champion uses a temporary `rag_harness_*` Qdrant collection, promote/rebuild that selected champion into the normal deployment collection after research. The deployment rebuild is not part of the experimental five-build budget.",
    ]
    (state_dir / "FINAL_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Planner -> Implementer -> Reviewer -> Fixer research loop for rag_trend."
    )
    parser.add_argument("--holdout", required=True, help="Blind harness dataset outside this repository.")
    parser.add_argument("--state-dir", default=None, help="Persistent run state outside this repository.")
    parser.add_argument("--max-cycles", type=int, default=None)
    parser.add_argument("--planner-model", default=None)
    parser.add_argument("--reviewer-model", default=None)
    parser.add_argument("--worker-model", default=None)
    parser.add_argument("--fixer-model", default=None)
    parser.add_argument("--push", action="store_true", help="Push accepted champion commits.")
    parser.add_argument("--fresh", action="store_true", help="Delete previous external state before starting.")
    return parser


def _apply_cli_overrides(config: dict[str, Any], args: argparse.Namespace) -> None:
    if args.max_cycles is not None:
        config["max_cycles"] = args.max_cycles
    for name in ("planner_model", "reviewer_model", "worker_model", "fixer_model"):
        value = getattr(args, name)
        if value:
            config[name] = value


def main() -> int:
    args = _parser().parse_args()
    config = _read_json(CONFIG_PATH)
    _apply_cli_overrides(config, args)

    if not 1 <= int(config["max_cycles"]) <= 20:
        raise SystemExit("max_cycles must be in 1..20")
    if not 0 <= int(config["max_index_builds"]) <= 5:
        raise SystemExit("max_index_builds must be in 0..5")
    if not clean():
        raise SystemExit("Working tree must be clean. Use a dedicated clone/worktree.")
    current_branch = branch()
    if current_branch in {"main", "master"}:
        raise SystemExit("Refusing autonomous edits on main/master. Checkout the harness branch first.")

    holdout = Path(args.holdout).expanduser().resolve()
    if not holdout.exists():
        raise SystemExit(f"Blind holdout not found: {holdout}")
    check_dataset_outside_repo(holdout)

    state_dir = (
        Path(args.state_dir).expanduser().resolve()
        if args.state_dir
        else Path.home() / ".rag-trend-harness" / current_branch.replace("/", "__")
    )
    ensure_outside_repo(state_dir, "Harness state directory")
    if args.fresh and state_dir.exists():
        import shutil

        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    goal = GOAL_PATH.read_text(encoding="utf-8")
    research = RESEARCH_PATH.read_text(encoding="utf-8")
    taxonomy = (ROOT / str(config["taxonomy"])).read_text(encoding="utf-8")
    state_path = state_dir / "state.json"

    if state_path.exists():
        state = _read_json(state_path)
        saved_champion = str(state.get("champion_commit") or "")
        if saved_champion and head() != saved_champion:
            raise SystemExit(
                f"HEAD differs from saved champion {saved_champion}. Resume from that commit or use --fresh."
            )
    else:
        print("\n=== BASELINE ===", flush=True)
        state = _baseline(config=config, holdout=holdout, state_dir=state_dir)
        print("Public baseline:", json.dumps(state["baseline_public"], ensure_ascii=False, indent=2))
        print("Blind baseline:", json.dumps(_visible_hidden(state["baseline_hidden"]), ensure_ascii=False, indent=2))

    max_cycles = int(config["max_cycles"])
    floor = float(config["coverage_floor"])

    for cycle in range(int(state.get("cycle", 0)) + 1, max_cycles + 1):
        state["cycle"] = cycle
        write_json(state_path, state)
        run_dir = state_dir / "runs" / f"{cycle:03d}"
        run_dir.mkdir(parents=True, exist_ok=True)

        champion_commit = str(state["champion_commit"])
        champion_alias = state.get("champion_collection_alias") or None
        candidate_alias = champion_alias
        champion_public = Metrics.from_summary(state["champion_public"])
        champion_hidden = Metrics.from_summary(state["champion_hidden"])

        print(f"\n######## CYCLE {cycle}/{max_cycles} ########", flush=True)

        plan = run_codex(
            role="planner",
            prompt=planner_prompt(
                cycle=cycle,
                goal=goal,
                research_context=research,
                taxonomy=taxonomy,
                history=_compact_history(list(state.get("history") or [])),
                public_failures=list(state.get("champion_public_failures") or []),
                public_metrics=dict(state["champion_public"]),
                hidden_metrics=_visible_hidden(dict(state["champion_hidden"])),
                index_builds_used=int(state.get("index_builds_used", 0)),
                max_index_builds=int(config["max_index_builds"]),
                coverage_floor=floor,
            ),
            schema=PLANNER_SCHEMA,
            model=str(config["planner_model"]),
            effort=str(config["planner_reasoning_effort"]),
            sandbox="workspace-write",
            run_dir=run_dir,
            network=False,
            qdrant_alias=champion_alias,
        )
        write_json(run_dir / "plan.json", plan)

        if plan.get("action") == "DONE":
            rollback(champion_commit)
            if final_goal_met(hidden=champion_hidden, coverage_floor=floor):
                _record(
                    state_dir,
                    state,
                    _row(
                        cycle=cycle,
                        plan=plan,
                        decision="DONE",
                        reason=plan.get("why_now", "Planner finished."),
                        public_rate=champion_public.hard_pass_rate,
                        blind_rate=champion_hidden.hard_pass_rate,
                        state=state,
                        candidate_alias=champion_alias,
                    ),
                )
                _write_final_report(state_dir=state_dir, state=state, config=config, outcome="DONE")
                return 0
            _record(
                state_dir,
                state,
                _row(
                    cycle=cycle,
                    plan=plan,
                    decision="CONTINUE",
                    reason="Planner requested DONE before the blind 93% floor was reached.",
                    public_rate=champion_public.hard_pass_rate,
                    blind_rate=champion_hidden.hard_pass_rate,
                    state=state,
                    candidate_alias=champion_alias,
                ),
            )
            continue

        if not planner_changes_are_scoped():
            paths = sorted(changed_paths())
            rollback(champion_commit)
            raise HarnessError(
                "Planner may edit only OpenSpec planning artifacts under openspec/changes/. "
                f"Changed: {paths}"
            )
        change_name = str(plan.get("change_name") or "")
        if not change_name or not (ROOT / "openspec" / "changes" / change_name).exists():
            rollback(champion_commit)
            raise HarnessError(f"Planner did not create openspec/changes/{change_name}")

        worker = run_codex(
            role="implementer",
            prompt=worker_prompt(goal=goal, plan=plan),
            schema=WORKER_SCHEMA,
            model=str(config["worker_model"]),
            effort=str(config["worker_reasoning_effort"]),
            sandbox="workspace-write",
            run_dir=run_dir,
            network=True,
            qdrant_alias=champion_alias,
        )
        write_json(run_dir / "worker.json", worker)

        if worker.get("status") != "complete":
            _reject(
                state_dir=state_dir,
                state=state,
                champion_commit=champion_commit,
                cycle=cycle,
                plan=plan,
                reason=worker.get("blocker") or "Implementer blocked.",
                champion_public=champion_public,
                champion_hidden=champion_hidden,
                candidate_alias=candidate_alias,
            )
            continue

        forbidden = protected_changes()
        if forbidden:
            _reject(
                state_dir=state_dir,
                state=state,
                champion_commit=champion_commit,
                cycle=cycle,
                plan=plan,
                reason=f"Implementer changed protected harness/GOLD files: {forbidden}",
                champion_public=champion_public,
                champion_hidden=champion_hidden,
                candidate_alias=candidate_alias,
            )
            continue

        tests_ok, test_tail = run_tests(config, run_dir, "implementer", candidate_alias)
        if tests_ok and bool(worker.get("needs_reindex")):
            try:
                candidate_alias = rebuild_index(
                    config,
                    state,
                    run_dir,
                    cycle=cycle,
                    reason="implementer requested index rebuild",
                )
                write_json(state_path, state)
            except Exception as exc:
                tests_ok = False
                test_tail = str(exc)

        public_after_worker: dict[str, Any] | None = None
        if tests_ok:
            try:
                public_after_worker = run_eval(
                    dataset=ROOT / str(config["public_dataset"]),
                    output_dir=run_dir,
                    tag="public_after_worker",
                    env_overrides=collection_env(candidate_alias),
                )
            except Exception as exc:
                test_tail = f"Public evaluation failed: {exc}"

        reviewer_metrics = (
            dict(public_after_worker["summary"]) if public_after_worker else {}
        )
        reviewer_failures = (
            list(public_after_worker["failures"])
            if public_after_worker
            else [{"id": "TEST_OR_EVAL", "verdict": "FAIL", "reason": test_tail}]
        )
        diff_text = git("diff", "--").stdout
        (run_dir / "candidate.diff").write_text(diff_text, encoding="utf-8")

        review = run_codex(
            role="reviewer",
            prompt=reviewer_prompt(
                goal=goal,
                plan=plan,
                worker_result=worker,
                diff_text=diff_text,
                public_metrics=reviewer_metrics,
                public_failures=reviewer_failures,
            ),
            schema=REVIEW_SCHEMA,
            model=str(config["reviewer_model"]),
            effort=str(config["reviewer_reasoning_effort"]),
            sandbox="read-only",
            run_dir=run_dir,
            network=False,
            qdrant_alias=candidate_alias,
        )
        write_json(run_dir / "review.json", review)

        if review.get("decision") == "REJECT":
            _reject(
                state_dir=state_dir,
                state=state,
                champion_commit=champion_commit,
                cycle=cycle,
                plan=plan,
                reason=review.get("summary") or "Reviewer rejected candidate.",
                champion_public=champion_public,
                champion_hidden=champion_hidden,
                review_decision="REJECT",
                candidate_alias=candidate_alias,
            )
            continue

        final_public = public_after_worker
        if review.get("decision") == "FIX":
            fixer = run_codex(
                role="fixer",
                prompt=fixer_prompt(goal=goal, plan=plan, review=review),
                schema=WORKER_SCHEMA,
                model=str(config["fixer_model"]),
                effort=str(config["fixer_reasoning_effort"]),
                sandbox="workspace-write",
                run_dir=run_dir,
                network=True,
                qdrant_alias=candidate_alias,
            )
            write_json(run_dir / "fixer.json", fixer)

            if fixer.get("status") != "complete":
                _reject(
                    state_dir=state_dir,
                    state=state,
                    champion_commit=champion_commit,
                    cycle=cycle,
                    plan=plan,
                    reason=fixer.get("blocker") or "Fixer blocked.",
                    champion_public=champion_public,
                    champion_hidden=champion_hidden,
                    review_decision="FIX",
                    candidate_alias=candidate_alias,
                )
                continue

            forbidden = protected_changes()
            if forbidden:
                _reject(
                    state_dir=state_dir,
                    state=state,
                    champion_commit=champion_commit,
                    cycle=cycle,
                    plan=plan,
                    reason=f"Fixer changed protected harness/GOLD files: {forbidden}",
                    champion_public=champion_public,
                    champion_hidden=champion_hidden,
                    review_decision="FIX",
                    candidate_alias=candidate_alias,
                )
                continue

            tests_ok, test_tail = run_tests(config, run_dir, "fixer", candidate_alias)
            if not tests_ok:
                _reject(
                    state_dir=state_dir,
                    state=state,
                    champion_commit=champion_commit,
                    cycle=cycle,
                    plan=plan,
                    reason=f"Tests failed after fixer: {test_tail[-1000:]}",
                    champion_public=champion_public,
                    champion_hidden=champion_hidden,
                    review_decision="FIX",
                    candidate_alias=candidate_alias,
                )
                continue

            if bool(fixer.get("needs_reindex")):
                try:
                    candidate_alias = rebuild_index(
                        config,
                        state,
                        run_dir,
                        cycle=cycle,
                        reason="fixer requested index rebuild",
                    )
                    write_json(state_path, state)
                except Exception as exc:
                    _reject(
                        state_dir=state_dir,
                        state=state,
                        champion_commit=champion_commit,
                        cycle=cycle,
                        plan=plan,
                        reason=str(exc),
                        champion_public=champion_public,
                        champion_hidden=champion_hidden,
                        review_decision="FIX",
                        candidate_alias=candidate_alias,
                    )
                    continue

            try:
                final_public = run_eval(
                    dataset=ROOT / str(config["public_dataset"]),
                    output_dir=run_dir,
                    tag="public_after_fixer",
                    env_overrides=collection_env(candidate_alias),
                )
            except Exception as exc:
                _reject(
                    state_dir=state_dir,
                    state=state,
                    champion_commit=champion_commit,
                    cycle=cycle,
                    plan=plan,
                    reason=f"Public eval after fixer failed: {exc}",
                    champion_public=champion_public,
                    champion_hidden=champion_hidden,
                    review_decision="FIX",
                    candidate_alias=candidate_alias,
                )
                continue

        if final_public is None:
            _reject(
                state_dir=state_dir,
                state=state,
                champion_commit=champion_commit,
                cycle=cycle,
                plan=plan,
                reason=f"Candidate did not pass tests/public eval: {test_tail[-1000:]}",
                champion_public=champion_public,
                champion_hidden=champion_hidden,
                review_decision=str(review.get("decision")),
                candidate_alias=candidate_alias,
            )
            continue

        hidden = run_hidden_eval(
            dataset=holdout,
            output_dir=run_dir,
            tag="hidden_final",
            env_overrides=collection_env(candidate_alias),
        )
        candidate_public = Metrics.from_summary(final_public["summary"])
        candidate_hidden = Metrics.from_summary(hidden["summary"])
        accepted, reason = accept_candidate(
            candidate_public=candidate_public,
            candidate_hidden=candidate_hidden,
            champion_public=champion_public,
            champion_hidden=champion_hidden,
        )
        write_json(
            run_dir / "gate.json",
            {
                "accepted": accepted,
                "reason": reason,
                "public": final_public["summary"],
                "blind": _visible_hidden(hidden["summary"]),
                "coverage_floor": floor,
                "candidate_collection_alias": candidate_alias,
            },
        )

        if accepted:
            git("add", "-A")
            git("commit", "-m", f"harness: cycle {cycle:02d} {change_name}")
            new_champion = head()
            if args.push:
                git("push", "origin", current_branch)
            state["champion_commit"] = new_champion
            state["champion_collection_alias"] = candidate_alias
            state["champion_public"] = final_public["summary"]
            state["champion_hidden"] = hidden["summary"]
            state["champion_public_failures"] = final_public["failures"]
            decision = "ACCEPTED"
        else:
            rollback(champion_commit)
            decision = "REJECTED"

        _record(
            state_dir,
            state,
            _row(
                cycle=cycle,
                plan=plan,
                decision=decision,
                reason=reason,
                public_rate=candidate_public.hard_pass_rate,
                blind_rate=candidate_hidden.hard_pass_rate,
                state=state,
                review_decision=str(review.get("decision")),
                candidate_alias=candidate_alias,
            ),
        )

    final_hidden = Metrics.from_summary(state["champion_hidden"])
    outcome = (
        "MAX_CYCLES_GOAL_MET"
        if final_goal_met(hidden=final_hidden, coverage_floor=floor)
        else "MAX_CYCLES"
    )
    _write_final_report(state_dir=state_dir, state=state, config=config, outcome=outcome)
    return 0 if outcome == "MAX_CYCLES_GOAL_MET" else 2


if __name__ == "__main__":
    raise SystemExit(main())
