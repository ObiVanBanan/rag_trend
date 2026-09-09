from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import supervisor

orchestrator = supervisor.orchestrator


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise orchestrator.HarnessError(f"Expected JSON object in {path}")
    return payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resume an interrupted RAG harness cycle from the last completed stage."
    )
    parser.add_argument("--holdout", required=True, help="Blind harness dataset outside this repository.")
    parser.add_argument("--state-dir", default=None, help="Persistent run state outside this repository.")
    parser.add_argument("--max-cycles", type=int, default=None)
    parser.add_argument("--planner-model", default=None)
    parser.add_argument("--reviewer-model", default=None)
    parser.add_argument("--worker-model", default=None)
    parser.add_argument("--fixer-model", default=None)
    parser.add_argument("--push", action="store_true", help="Push accepted champion commits.")
    parser.add_argument("--resume", action="store_true", help="Resume the current interrupted cycle.")
    parser.add_argument("--fresh", action="store_true", help=argparse.SUPPRESS)
    return parser


def _state_dir(args: argparse.Namespace, current_branch: str) -> Path:
    if args.state_dir:
        return Path(args.state_dir).expanduser().resolve()
    return Path.home() / ".rag-trend-harness" / current_branch.replace("/", "__")


def _harness_only_path(path: str) -> bool:
    return (
        path.startswith("harness_rag/")
        or path == "scripts/run_rag_harness.py"
        or path.startswith("tests/test_rag_harness_")
    )


def _adopt_harness_only_head_update(state: dict[str, Any], state_path: Path) -> str:
    """Allow pulling resume/harness code after a crash without invalidating product metrics."""
    saved = str(state.get("champion_commit") or "")
    current = orchestrator.head()
    if not saved or current == saved:
        return current

    diff = orchestrator.git("diff", "--name-only", f"{saved}..{current}", check=False)
    if diff.returncode != 0:
        raise orchestrator.HarnessError(
            f"Cannot compare saved champion {saved} with current HEAD {current}."
        )
    changed = [line.strip().replace("\\", "/") for line in diff.stdout.splitlines() if line.strip()]
    if not changed or not all(_harness_only_path(path) for path in changed):
        raise orchestrator.HarnessError(
            "HEAD changed since the interrupted cycle in product/non-harness files. "
            f"Saved champion={saved}, current={current}, changed={changed}"
        )

    print(
        f"Adopting harness-only HEAD update {saved[:12]} -> {current[:12]} "
        "without changing saved product metrics.",
        flush=True,
    )
    state["champion_commit"] = current
    orchestrator.write_json(state_path, state)
    return current


def _detect_resume_stage(run_dir: Path) -> str:
    if not (run_dir / "plan.json").exists():
        return "planner"
    if not (run_dir / "worker.json").exists():
        return "implementer"
    if not (run_dir / "public_after_worker.json").exists():
        return "worker_postcheck"
    if not (run_dir / "review.json").exists():
        return "reviewer"

    review = _read_json(run_dir / "review.json")
    decision = str(review.get("decision") or "")
    if decision == "REJECT":
        return "review_reject"
    if decision == "FIX":
        if not (run_dir / "fixer.json").exists():
            return "fixer"
        if not (run_dir / "public_after_fixer.json").exists():
            return "fixer_postcheck"
    if not (run_dir / "gate.json").exists():
        return "gate"
    return "complete"


def _candidate_alias(state: dict[str, Any], cycle: int) -> str | None:
    """Best-effort recovery of a temporary collection used by the interrupted cycle."""
    champion_alias = state.get("champion_collection_alias") or None
    history = list(state.get("index_history") or [])
    if not history:
        return champion_alias
    last = history[-1]
    collection = last.get("collection") if isinstance(last, dict) else None
    if not collection:
        return champion_alias
    prefix = f"rag_harness_c{cycle:02d}_"
    return str(collection) if str(collection).startswith(prefix) else champion_alias


def _load_public_result(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        raise orchestrator.HarnessError(f"Missing summary in {path}")
    rows = payload.get("results") or []
    failures = [
        {
            "id": row.get("id"),
            "verdict": row.get("verdict"),
            "reason": row.get("reason"),
        }
        for row in rows
        if isinstance(row, dict) and row.get("verdict") != "PASS"
    ]
    return {"summary": dict(summary), "failures": failures}


def _continue_normal_run() -> int:
    """Continue with cycle N+1 using the ordinary supervisor after the resumed cycle closes."""
    sys.argv = [arg for arg in sys.argv if arg not in {"--resume", "--fresh"}]
    return supervisor.main()


def _reject_and_continue(
    *,
    state_dir: Path,
    state: dict[str, Any],
    champion_commit: str,
    cycle: int,
    plan: dict[str, Any],
    reason: str,
    champion_public: Any,
    champion_hidden: Any,
    review_decision: str | None = None,
    candidate_alias: str | None = None,
) -> int:
    orchestrator._reject(
        state_dir=state_dir,
        state=state,
        champion_commit=champion_commit,
        cycle=cycle,
        plan=plan,
        reason=reason,
        champion_public=champion_public,
        champion_hidden=champion_hidden,
        review_decision=review_decision,
        candidate_alias=candidate_alias,
    )
    return _continue_normal_run()


def main() -> int:
    args = _parser().parse_args()
    if not args.resume:
        raise SystemExit("Use --resume with the resume entrypoint.")
    if args.fresh:
        raise SystemExit("--resume and --fresh are mutually exclusive.")

    current_branch = orchestrator.branch()
    if current_branch in {"main", "master"}:
        raise SystemExit("Refusing autonomous edits on main/master. Checkout the harness branch first.")

    holdout = Path(args.holdout).expanduser().resolve()
    if not holdout.exists():
        raise SystemExit(f"Blind holdout not found: {holdout}")
    orchestrator.check_dataset_outside_repo(holdout)

    state_dir = _state_dir(args, current_branch)
    orchestrator.ensure_outside_repo(state_dir, "Harness state directory")
    state_path = state_dir / "state.json"
    if not state_path.exists():
        raise SystemExit(f"No harness state to resume: {state_path}")

    state = _read_json(state_path)
    config = _read_json(orchestrator.CONFIG_PATH)
    orchestrator._apply_cli_overrides(config, args)

    cycle = int(state.get("cycle", 0))
    if cycle <= 0:
        raise SystemExit("No interrupted research cycle is recorded in state.json.")
    run_dir = state_dir / "runs" / f"{cycle:03d}"
    if not run_dir.exists():
        raise SystemExit(f"Run directory missing for cycle {cycle}: {run_dir}")

    champion_commit = _adopt_harness_only_head_update(state, state_path)
    champion_public = orchestrator.Metrics.from_summary(dict(state["champion_public"]))
    champion_hidden = orchestrator.Metrics.from_summary(dict(state["champion_hidden"]))
    candidate_alias = _candidate_alias(state, cycle)
    stage = _detect_resume_stage(run_dir)

    print(f"\n=== RESUME CYCLE {cycle} | stage={stage} ===", flush=True)

    if stage == "complete":
        raise orchestrator.HarnessError(
            "The current cycle already has gate.json. Refusing to resume it twice."
        )

    if stage == "planner":
        if orchestrator.changed_paths():
            raise orchestrator.HarnessError(
                "Cycle has no saved plan.json but the worktree is dirty; cannot safely infer planner state."
            )
        state["cycle"] = cycle - 1
        orchestrator.write_json(state_path, state)
        return _continue_normal_run()

    plan = _read_json(run_dir / "plan.json")
    change_name = str(plan.get("change_name") or "")
    if plan.get("action") == "DONE":
        raise orchestrator.HarnessError(
            "Interrupted cycle contains Planner DONE; stage resume is only for implementation/evaluation stages."
        )
    if not change_name:
        raise orchestrator.HarnessError("Saved plan has no change_name.")

    change_dir = orchestrator.ROOT / "openspec" / "changes" / change_name
    if not change_dir.exists():
        raise orchestrator.HarnessError(
            f"Interrupted candidate is missing openspec/changes/{change_name}. "
            "If you stashed it, restore the stash before --resume."
        )

    if stage == "implementer":
        worker = orchestrator.run_codex(
            role="implementer",
            prompt=orchestrator.worker_prompt(
                goal=orchestrator.GOAL_PATH.read_text(encoding="utf-8"), plan=plan
            ),
            schema=orchestrator.WORKER_SCHEMA,
            model=str(config["worker_model"]),
            effort=str(config["worker_reasoning_effort"]),
            sandbox="workspace-write",
            run_dir=run_dir,
            network=True,
            qdrant_alias=candidate_alias,
        )
        orchestrator.write_json(run_dir / "worker.json", worker)
        stage = "worker_postcheck"
    else:
        worker = _read_json(run_dir / "worker.json")

    if worker.get("status") != "complete":
        return _reject_and_continue(
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

    forbidden = orchestrator.protected_changes()
    if forbidden:
        return _reject_and_continue(
            state_dir=state_dir,
            state=state,
            champion_commit=champion_commit,
            cycle=cycle,
            plan=plan,
            reason=f"Interrupted candidate changed protected harness/GOLD files: {forbidden}",
            champion_public=champion_public,
            champion_hidden=champion_hidden,
            candidate_alias=candidate_alias,
        )

    public_after_worker: dict[str, Any] | None = None
    if (run_dir / "public_after_worker.json").exists():
        public_after_worker = _load_public_result(run_dir / "public_after_worker.json")

    if stage == "worker_postcheck":
        tests_ok, test_tail = orchestrator.run_tests(
            config, run_dir, "implementer_resume", candidate_alias
        )
        if not tests_ok:
            return _reject_and_continue(
                state_dir=state_dir,
                state=state,
                champion_commit=champion_commit,
                cycle=cycle,
                plan=plan,
                reason=f"Tests failed after resumed implementer: {test_tail[-1000:]}",
                champion_public=champion_public,
                champion_hidden=champion_hidden,
                candidate_alias=candidate_alias,
            )
        if bool(worker.get("needs_reindex")) and candidate_alias == (
            state.get("champion_collection_alias") or None
        ):
            candidate_alias = orchestrator.rebuild_index(
                config,
                state,
                run_dir,
                cycle=cycle,
                reason="resumed implementer requested index rebuild",
            )
            orchestrator.write_json(state_path, state)
        public_after_worker = orchestrator.run_eval(
            dataset=orchestrator.ROOT / str(config["public_dataset"]),
            output_dir=run_dir,
            tag="public_after_worker",
            env_overrides=orchestrator.collection_env(candidate_alias),
        )
        stage = "reviewer"

    if public_after_worker is None:
        raise orchestrator.HarnessError(
            "Cannot resume reviewer/fixer without public_after_worker evaluation."
        )

    if stage == "reviewer":
        diff_text = orchestrator.git("diff", "--").stdout
        (run_dir / "candidate.diff").write_text(diff_text, encoding="utf-8")
        review = orchestrator.run_codex(
            role="reviewer",
            prompt=orchestrator.reviewer_prompt(
                goal=orchestrator.GOAL_PATH.read_text(encoding="utf-8"),
                plan=plan,
                worker_result=worker,
                diff_text=diff_text,
                public_metrics=dict(public_after_worker["summary"]),
                public_failures=list(public_after_worker["failures"]),
            ),
            schema=orchestrator.REVIEW_SCHEMA,
            model=str(config["reviewer_model"]),
            effort=str(config["reviewer_reasoning_effort"]),
            sandbox="read-only",
            run_dir=run_dir,
            network=False,
            qdrant_alias=candidate_alias,
        )
        orchestrator.write_json(run_dir / "review.json", review)
    else:
        review = _read_json(run_dir / "review.json")

    if review.get("decision") == "REJECT":
        return _reject_and_continue(
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

    final_public = public_after_worker

    if review.get("decision") == "FIX":
        if not (run_dir / "fixer.json").exists():
            fixer = orchestrator.run_codex(
                role="fixer",
                prompt=orchestrator.fixer_prompt(
                    goal=orchestrator.GOAL_PATH.read_text(encoding="utf-8"),
                    plan=plan,
                    review=review,
                ),
                schema=orchestrator.WORKER_SCHEMA,
                model=str(config["fixer_model"]),
                effort=str(config["fixer_reasoning_effort"]),
                sandbox="workspace-write",
                run_dir=run_dir,
                network=True,
                qdrant_alias=candidate_alias,
            )
            orchestrator.write_json(run_dir / "fixer.json", fixer)
        else:
            fixer = _read_json(run_dir / "fixer.json")

        if fixer.get("status") != "complete":
            return _reject_and_continue(
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

        forbidden = orchestrator.protected_changes()
        if forbidden:
            return _reject_and_continue(
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

        if (run_dir / "public_after_fixer.json").exists():
            final_public = _load_public_result(run_dir / "public_after_fixer.json")
        else:
            tests_ok, test_tail = orchestrator.run_tests(
                config, run_dir, "fixer_resume", candidate_alias
            )
            if not tests_ok:
                return _reject_and_continue(
                    state_dir=state_dir,
                    state=state,
                    champion_commit=champion_commit,
                    cycle=cycle,
                    plan=plan,
                    reason=f"Tests failed after resumed fixer: {test_tail[-1000:]}",
                    champion_public=champion_public,
                    champion_hidden=champion_hidden,
                    review_decision="FIX",
                    candidate_alias=candidate_alias,
                )
            if bool(fixer.get("needs_reindex")) and candidate_alias == (
                state.get("champion_collection_alias") or None
            ):
                candidate_alias = orchestrator.rebuild_index(
                    config,
                    state,
                    run_dir,
                    cycle=cycle,
                    reason="resumed fixer requested index rebuild",
                )
                orchestrator.write_json(state_path, state)
            final_public = orchestrator.run_eval(
                dataset=orchestrator.ROOT / str(config["public_dataset"]),
                output_dir=run_dir,
                tag="public_after_fixer",
                env_overrides=orchestrator.collection_env(candidate_alias),
            )

    gate_path = run_dir / "gate.json"
    if gate_path.exists():
        raise orchestrator.HarnessError(
            "This cycle already has gate.json but was not fully closed. "
            "Refusing to double-commit or double-record it."
        )

    hidden = orchestrator.run_hidden_eval(
        dataset=holdout,
        output_dir=run_dir,
        tag="hidden_final",
        env_overrides=orchestrator.collection_env(candidate_alias),
    )
    candidate_public = orchestrator.Metrics.from_summary(dict(final_public["summary"]))
    candidate_hidden = orchestrator.Metrics.from_summary(dict(hidden["summary"]))
    accepted, reason = orchestrator.accept_candidate(
        candidate_public=candidate_public,
        candidate_hidden=candidate_hidden,
        champion_public=champion_public,
        champion_hidden=champion_hidden,
    )
    orchestrator.write_json(
        gate_path,
        {
            "accepted": accepted,
            "reason": reason,
            "public": final_public["summary"],
            "blind": orchestrator._visible_hidden(hidden["summary"]),
            "coverage_floor": float(config["coverage_floor"]),
            "candidate_collection_alias": candidate_alias,
            "resumed": True,
        },
    )

    if accepted:
        orchestrator.git("add", "-A")
        orchestrator.git("commit", "-m", f"harness: cycle {cycle:02d} {change_name}")
        new_champion = orchestrator.head()
        if args.push:
            orchestrator.git("push", "origin", current_branch)
        state["champion_commit"] = new_champion
        state["champion_collection_alias"] = candidate_alias
        state["champion_public"] = final_public["summary"]
        state["champion_hidden"] = hidden["summary"]
        state["champion_public_failures"] = final_public["failures"]
        decision = "ACCEPTED"
    else:
        orchestrator.rollback(champion_commit)
        decision = "REJECTED"

    orchestrator._record(
        state_dir,
        state,
        orchestrator._row(
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

    print(
        f"\n--- RESUMED CYCLE {cycle} {decision} ---\n"
        f"Public hard-pass: {candidate_public.hard_pass_rate:.3f}\n"
        f"Blind hard-pass:  {candidate_hidden.hard_pass_rate:.3f}\n"
        f"Reason: {reason}",
        flush=True,
    )

    return _continue_normal_run()


if __name__ == "__main__":
    raise SystemExit(main())
