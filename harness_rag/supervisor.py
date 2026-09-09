from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
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


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(__file__).resolve().with_name("config.json")
GOAL_PATH = Path(__file__).resolve().with_name("GOAL.md")
RESEARCH_PATH = Path(__file__).resolve().with_name("RESEARCH_CONTEXT.md")

PROTECTED_EXACT = {
    "ld_products_full_nomenclature.csv",
    "scripts/build_index.py",
    "scripts/eval_harness_gold.py",
    "src/nomenclature_matcher/golden_rules.py",
    "src/nomenclature_matcher/harness_gold.py",
}
PROTECTED_PREFIXES = (
    "harness_rag/",
    "data/",
)


class HarnessError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _run(
    args: list[str],
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    print("$ " + " ".join(shlex.quote(arg) for arg in args), flush=True)
    result = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        timeout=timeout,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if check and result.returncode != 0:
        raise HarnessError(f"command failed ({result.returncode}): {' '.join(args)}")
    return result


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(["git", *args], check=check)


def _head() -> str:
    return _git("rev-parse", "HEAD").stdout.strip()


def _branch() -> str:
    return _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def _clean() -> bool:
    return not _git("status", "--porcelain").stdout.strip()


def _changed_paths() -> set[str]:
    tracked = _git("diff", "--name-only", "HEAD", "--").stdout.splitlines()
    untracked = _git("ls-files", "--others", "--exclude-standard").stdout.splitlines()
    return {path.strip().replace("\\", "/") for path in [*tracked, *untracked] if path.strip()}


def _protected_changes(paths: set[str]) -> list[str]:
    protected: list[str] = []
    for path in sorted(paths):
        if path in PROTECTED_EXACT or any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES):
            protected.append(path)
    return protected


def _planner_changes_are_scoped(paths: set[str]) -> bool:
    return bool(paths) and all(path.startswith("openspec/changes/") for path in paths)


def _rollback(commit: str) -> None:
    print(f"Rolling back to champion {commit[:12]}", flush=True)
    _git("reset", "--hard", commit)
    _git("clean", "-fd")


def _agent_env() -> dict[str, str]:
    env = os.environ.copy()
    env["RAG_HARNESS_AGENT"] = "1"
    # A holdout path/key should never be passed to Codex through the environment.
    for key in list(env):
        if key.startswith("RAG_HARNESS_HOLDOUT") or key.startswith("RAG_HOLDOUT"):
            env.pop(key, None)
    return env


def _run_codex(
    *,
    role: str,
    prompt: str,
    schema: dict[str, Any],
    model: str,
    reasoning_effort: str,
    sandbox: str,
    run_dir: Path,
    network: bool,
) -> dict[str, Any]:
    role_dir = run_dir / role
    role_dir.mkdir(parents=True, exist_ok=True)
    schema_path = role_dir / "schema.json"
    result_path = role_dir / "result.json"
    log_path = role_dir / "codex.log"
    _write_json(schema_path, schema)
    result_path.unlink(missing_ok=True)

    args = [
        "codex",
        "exec",
        "-m",
        model,
        "--sandbox",
        sandbox,
        "--ephemeral",
        "--color",
        "never",
        "--config",
        'approval_policy="never"',
        "--config",
        f'model_reasoning_effort="{reasoning_effort}"',
    ]
    if network and sandbox == "workspace-write":
        args += ["--config", "sandbox_workspace_write.network_access=true"]
    args += [
        "--output-schema",
        str(schema_path),
        "--output-last-message",
        str(result_path),
        "-",
    ]

    print(f"\n=== {role.upper()} | {model} | reasoning={reasoning_effort} ===", flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(
            args,
            cwd=ROOT,
            text=True,
            input=prompt,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=_agent_env(),
        )
    if result.returncode != 0:
        raise HarnessError(f"{role} Codex exited {result.returncode}; see {log_path}")
    if not result_path.exists():
        raise HarnessError(f"{role} did not produce structured result")
    try:
        payload = _read_json(result_path)
    except json.JSONDecodeError as exc:
        raise HarnessError(f"{role} returned invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise HarnessError(f"{role} result is not an object")
    return payload


def _run_tests(config: dict[str, Any], run_dir: Path, tag: str) -> tuple[bool, str]:
    cmd = [str(part) for part in config["test_command"]]
    result = _run(cmd, check=False, env=os.environ.copy())
    text = result.stdout or ""
    (run_dir / f"{tag}_tests.log").write_text(text, encoding="utf-8")
    return result.returncode == 0, text[-12000:]


def _maybe_reindex(config: dict[str, Any], state: dict[str, Any], run_dir: Path, reason: str) -> None:
    used = int(state.get("index_builds_used", 0))
    limit = int(config["max_index_builds"])
    if used >= limit:
        raise HarnessError(f"index rebuild requested but global budget is exhausted ({used}/{limit})")
    env = os.environ.copy()
    env.pop("RAG_HARNESS_AGENT", None)
    result = _run([str(part) for part in config["index_command"]], check=False, env=env)
    (run_dir / f"reindex_{used + 1}.log").write_text(result.stdout or "", encoding="utf-8")
    if result.returncode != 0:
        raise HarnessError("full index rebuild failed")
    state["index_builds_used"] = used + 1
    state.setdefault("index_history", []).append({"at": _now(), "reason": reason, "count": used + 1})


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


def _history_summary(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for row in history[-8:]:
        compact.append(
            {
                "cycle": row.get("cycle"),
                "hypothesis": row.get("hypothesis"),
                "decision": row.get("decision"),
                "reason": row.get("reason"),
                "public_hard_pass_rate": row.get("public_hard_pass_rate"),
                "blind_hard_pass_rate": row.get("blind_hard_pass_rate"),
                "index_builds_used": row.get("index_builds_used"),
            }
        )
    return compact


def _record_history(state_dir: Path, state: dict[str, Any], row: dict[str, Any]) -> None:
    history = list(state.get("history") or [])
    history.append(row)
    state["history"] = history
    _write_json(state_dir / "state.json", state)
    with (state_dir / "history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _baseline(config: dict[str, Any], holdout: Path, state_dir: Path) -> dict[str, Any]:
    eval_dir = state_dir / "eval"
    public = run_eval(
        dataset=ROOT / str(config["public_dataset"]),
        output_dir=eval_dir,
        tag="baseline_public",
    )
    hidden = run_hidden_eval(dataset=holdout, output_dir=eval_dir, tag="baseline_hidden")
    state = {
        "version": 1,
        "started_at": _now(),
        "branch": _branch(),
        "baseline_commit": _head(),
        "champion_commit": _head(),
        "cycle": 0,
        "index_builds_used": 0,
        "baseline_public": public["summary"],
        "baseline_hidden": hidden["summary"],
        "champion_public": public["summary"],
        "champion_hidden": hidden["summary"],
        "champion_public_failures": public["failures"],
        "history": [],
    }
    _write_json(state_dir / "state.json", state)
    return state


def _write_final_report(state_dir: Path, state: dict[str, Any], config: dict[str, Any], outcome: str) -> None:
    hidden = state.get("champion_hidden") or {}
    public = state.get("champion_public") or {}
    lines = [
        "# RAG Harness Final Report",
        "",
        f"- Outcome: **{outcome}**",
        f"- Cycles completed: **{state.get('cycle', 0)}** / {config['max_cycles']}",
        f"- Index rebuilds: **{state.get('index_builds_used', 0)}** / {config['max_index_builds']}",
        f"- Champion commit: `{state.get('champion_commit', '')}`",
        f"- Public hard-pass rate: **{public.get('hard_pass_rate')}**",
        f"- Blind hard-pass rate: **{hidden.get('hard_pass_rate')}**",
        f"- Required blind floor: **{float(config['coverage_floor']):.2%}**",
        "",
        "## Hypothesis history",
        "",
    ]
    for row in state.get("history", []):
        lines.append(
            f"- Cycle {row.get('cycle')}: {row.get('decision')} — {row.get('hypothesis', '')} "
            f"(public={row.get('public_hard_pass_rate')}, blind={row.get('blind_hard_pass_rate')})"
        )
    (state_dir / "FINAL_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Autonomous planner/implementer/reviewer/fixer loop for rag_trend.")
    parser.add_argument("--holdout", required=True, help="Blind harness dataset outside the repository.")
    parser.add_argument("--state-dir", default=None, help="Persistent harness state outside the repository.")
    parser.add_argument("--max-cycles", type=int, default=None)
    parser.add_argument("--planner-model", default=None)
    parser.add_argument("--reviewer-model", default=None)
    parser.add_argument("--worker-model", default=None)
    parser.add_argument("--fixer-model", default=None)
    parser.add_argument("--push", action="store_true", help="Push accepted champion commits to origin.")
    parser.add_argument("--fresh", action="store_true", help="Start a fresh harness state directory.")
    return parser


def main() -> int:
    args = _parser().parse_args()
    config = _read_json(CONFIG_PATH)
    if args.max_cycles is not None:
        config["max_cycles"] = args.max_cycles
    for arg_name, config_name in (
        ("planner_model", "planner_model"),
        ("reviewer_model", "reviewer_model"),
        ("worker_model", "worker_model"),
        ("fixer_model", "fixer_model"),
    ):
        value = getattr(args, arg_name)
        if value:
            config[config_name] = value

    if int(config["max_cycles"]) > 15:
        raise SystemExit("max_cycles may not exceed 15")
    if int(config["max_index_builds"]) > 5:
        raise SystemExit("max_index_builds may not exceed 5")
    if not _clean():
        raise SystemExit("Working tree must be clean. Run the harness only in a dedicated clone/worktree.")
    branch = _branch()
    if branch in {"main", "master"}:
        raise SystemExit("Refusing to run autonomous edits on main/master. Checkout the dedicated harness branch first.")

    holdout = Path(args.holdout).expanduser().resolve()
    if not holdout.exists():
        raise SystemExit(f"Holdout not found: {holdout}")
    check_dataset_outside_repo(holdout)

    state_dir = (
        Path(args.state_dir).expanduser().resolve()
        if args.state_dir
        else Path.home() / ".rag-trend-harness" / branch.replace("/", "__")
    )
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
        champion = str(state.get("champion_commit") or "")
        if champion and _head() != champion:
            raise SystemExit(
                f"HEAD {_head()} differs from saved champion {champion}. Resume from the champion or use --fresh."
            )
    else:
        print("\n=== BASELINE ===")
        state = _baseline(config, holdout, state_dir)
        print("Baseline public:", json.dumps(state["baseline_public"], ensure_ascii=False, indent=2))
        print("Baseline blind:", json.dumps(_visible_hidden(state["baseline_hidden"]), ensure_ascii=False, indent=2))

    max_cycles = int(config["max_cycles"])
    coverage_floor = float(config["coverage_floor"])

    for cycle in range(int(state.get("cycle", 0)) + 1, max_cycles + 1):
        state["cycle"] = cycle
        _write_json(state_path, state)
        run_dir = state_dir / "runs" / f"{cycle:03d}"
        run_dir.mkdir(parents=True, exist_ok=True)
        champion_commit = str(state["champion_commit"])
        champion_public = Metrics.from_summary(state["champion_public"])
        champion_hidden = Metrics.from_summary(state["champion_hidden"])

        print(f"\n\n######## CYCLE {cycle}/{max_cycles} ########")
        plan = _run_codex(
            role="planner",
            prompt=planner_prompt(
                cycle=cycle,
                goal=goal,
                research_context=research,
                taxonomy=taxonomy,
                history=_history_summary(list(state.get("history") or [])),
                public_failures=list(state.get("champion_public_failures") or []),
                public_metrics=dict(state["champion_public"]),
                hidden_metrics=_visible_hidden(dict(state["champion_hidden"])),
                index_builds_used=int(state.get("index_builds_used", 0)),
                max_index_builds=int(config["max_index_builds"]),
                coverage_floor=coverage_floor,
            ),
            schema=PLANNER_SCHEMA,
            model=str(config["planner_model"]),
            reasoning_effort=str(config["planner_reasoning_effort"]),
            sandbox="workspace-write",
            run_dir=run_dir,
            network=False,
        )
        _write_json(run_dir / "plan.json", plan)

        planner_paths = _changed_paths()
        if plan.get("action") == "DONE":
            _rollback(champion_commit)
            if final_goal_met(hidden=champion_hidden, coverage_floor=coverage_floor):
                row = {
                    "cycle": cycle,
                    "hypothesis": plan.get("hypothesis"),
                    "decision": "DONE",
                    "reason": plan.get("why_now"),
                    "public_hard_pass_rate": champion_public.hard_pass_rate,
                    "blind_hard_pass_rate": champion_hidden.hard_pass_rate,
                    "index_builds_used": state.get("index_builds_used", 0),
                }
                _record_history(state_dir, state, row)
                _write_final_report(state_dir, state, config, "DONE")
                return 0
            row = {
                "cycle": cycle,
                "hypothesis": plan.get("hypothesis"),
                "decision": "CONTINUE",
                "reason": "Planner proposed DONE before the blind coverage floor was reached.",
                "public_hard_pass_rate": champion_public.hard_pass_rate,
                "blind_hard_pass_rate": champion_hidden.hard_pass_rate,
                "index_builds_used": state.get("index_builds_used", 0),
            }
            _record_history(state_dir, state, row)
            continue

        if not _planner_changes_are_scoped(planner_paths):
            _rollback(champion_commit)
            raise HarnessError(
                "Planner must only create OpenSpec planning artifacts under openspec/changes/. "
                f"Changed: {sorted(planner_paths)}"
            )
        change_name = str(plan.get("change_name") or "")
        if not change_name or not (ROOT / "openspec" / "changes" / change_name).exists():
            _rollback(champion_commit)
            raise HarnessError(f"Planner did not create openspec/changes/{change_name}")

        worker = _run_codex(
            role="implementer",
            prompt=worker_prompt(goal=goal, plan=plan),
            schema=WORKER_SCHEMA,
            model=str(config["worker_model"]),
            reasoning_effort=str(config["worker_reasoning_effort"]),
            sandbox="workspace-write",
            run_dir=run_dir,
            network=True,
        )
        _write_json(run_dir / "worker.json", worker)
        if worker.get("status") != "complete":
            _rollback(champion_commit)
            row = {
                "cycle": cycle,
                "hypothesis": plan.get("hypothesis"),
                "decision": "REJECTED",
                "reason": worker.get("blocker") or "Implementer blocked",
                "public_hard_pass_rate": champion_public.hard_pass_rate,
                "blind_hard_pass_rate": champion_hidden.hard_pass_rate,
                "index_builds_used": state.get("index_builds_used", 0),
            }
            _record_history(state_dir, state, row)
            continue

        protected = _protected_changes(_changed_paths())
        if protected:
            _rollback(champion_commit)
            row = {
                "cycle": cycle,
                "hypothesis": plan.get("hypothesis"),
                "decision": "REJECTED",
                "reason": f"Attempted to change protected evaluation/harness data: {protected}",
                "public_hard_pass_rate": champion_public.hard_pass_rate,
                "blind_hard_pass_rate": champion_hidden.hard_pass_rate,
                "index_builds_used": state.get("index_builds_used", 0),
            }
            _record_history(state_dir, state, row)
            continue

        tests_ok, test_tail = _run_tests(config, run_dir, "implementer")
        public_after_worker: dict[str, Any] | None = None
        if tests_ok:
            try:
                if bool(worker.get("needs_reindex")):
                    _maybe_reindex(config, state, run_dir, f"cycle {cycle} implementer request")
                    _write_json(state_path, state)
                public_after_worker = run_eval(
                    dataset=ROOT / str(config["public_dataset"]),
                    output_dir=run_dir,
                    tag="public_after_worker",
                )
            except Exception as exc:
                tests_ok = False
                test_tail = f"Post-implementation validation failed: {exc}"

        if public_after_worker is None:
            reviewer_metrics: dict[str, Any] = {}
            reviewer_failures = [{"id": "TEST_OR_EVAL", "verdict": "FAIL", "reason": test_tail}]
        else:
            reviewer_metrics = dict(public_after_worker["summary"])
            reviewer_failures = list(public_after_worker["failures"])

        diff_text = _git("diff", "--", check=True).stdout
        (run_dir / "candidate.diff").write_text(diff_text, encoding="utf-8")
        review = _run_codex(
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
            reasoning_effort=str(config["reviewer_reasoning_effort"]),
            sandbox="read-only",
            run_dir=run_dir,
            network=False,
        )
        _write_json(run_dir / "review.json", review)

        if review.get("decision") == "REJECT":
            _rollback(champion_commit)
            row = {
                "cycle": cycle,
                "hypothesis": plan.get("hypothesis"),
                "decision": "REJECTED",
                "reason": review.get("summary"),
                "public_hard_pass_rate": champion_public.hard_pass_rate,
                "blind_hard_pass_rate": champion_hidden.hard_pass_rate,
                "index_builds_used": state.get("index_builds_used", 0),
            }
            _record_history(state_dir, state, row)
            continue

        final_public = public_after_worker
        if review.get("decision") == "FIX":
            fixer = _run_codex(
                role="fixer",
                prompt=fixer_prompt(goal=goal, plan=plan, review=review),
                schema=WORKER_SCHEMA,
                model=str(config["fixer_model"]),
                reasoning_effort=str(config["fixer_reasoning_effort"]),
                sandbox="workspace-write",
                run_dir=run_dir,
                network=True,
            )
            _write_json(run_dir / "fixer.json", fixer)
            if fixer.get("status") != "complete":
                _rollback(champion_commit)
                row = {
                    "cycle": cycle,
                    "hypothesis": plan.get("hypothesis"),
                    "decision": "REJECTED",
                    "reason": fixer.get("blocker") or "Fixer blocked",
                    "public_hard_pass_rate": champion_public.hard_pass_rate,
                    "blind_hard_pass_rate": champion_hidden.hard_pass_rate,
                    "index_builds_used": state.get("index_builds_used", 0),
                }
                _record_history(state_dir, state, row)
                continue
            protected = _protected_changes(_changed_paths())
            if protected:
                _rollback(champion_commit)
                row = {
                    "cycle": cycle,
                    "hypothesis": plan.get("hypothesis"),
                    "decision": "REJECTED",
                    "reason": f"Fixer changed protected evaluation/harness data: {protected}",
                    "public_hard_pass_rate": champion_public.hard_pass_rate,
                    "blind_hard_pass_rate": champion_hidden.hard_pass_rate,
                    "index_builds_used": state.get("index_builds_used", 0),
                }
                _record_history(state_dir, state, row)
                continue
            tests_ok, test_tail = _run_tests(config, run_dir, "fixer")
            if not tests_ok:
                _rollback(champion_commit)
                row = {
                    "cycle": cycle,
                    "hypothesis": plan.get("hypothesis"),
                    "decision": "REJECTED",
                    "reason": f"Tests failed after fixer: {test_tail[-1000:]}",
                    "public_hard_pass_rate": champion_public.hard_pass_rate,
                    "blind_hard_pass_rate": champion_hidden.hard_pass_rate,
                    "index_builds_used": state.get("index_builds_used", 0),
                }
                _record_history(state_dir, state, row)
                continue
            if bool(fixer.get("needs_reindex")):
                try:
                    _maybe_reindex(config, state, run_dir, f"cycle {cycle} fixer request")
                    _write_json(state_path, state)
                except Exception as exc:
                    _rollback(champion_commit)
                    row = {
                        "cycle": cycle,
                        "hypothesis": plan.get("hypothesis"),
                        "decision": "REJECTED",
                        "reason": str(exc),
                        "public_hard_pass_rate": champion_public.hard_pass_rate,
                        "blind_hard_pass_rate": champion_hidden.hard_pass_rate,
                        "index_builds_used": state.get("index_builds_used", 0),
                    }
                    _record_history(state_dir, state, row)
                    continue
            final_public = run_eval(
                dataset=ROOT / str(config["public_dataset"]),
                output_dir=run_dir,
                tag="public_after_fixer",
            )

        if final_public is None:
            _rollback(champion_commit)
            row = {
                "cycle": cycle,
                "hypothesis": plan.get("hypothesis"),
                "decision": "REJECTED",
                "reason": "Candidate never reached a valid public evaluation.",
                "public_hard_pass_rate": champion_public.hard_pass_rate,
                "blind_hard_pass_rate": champion_hidden.hard_pass_rate,
                "index_builds_used": state.get("index_builds_used", 0),
            }
            _record_history(state_dir, state, row)
            continue

        # End-of-iteration blind hook. Only aggregate metrics survive this call.
        hidden_result = run_hidden_eval(dataset=holdout, output_dir=run_dir, tag="hidden_final")
        candidate_public = Metrics.from_summary(final_public["summary"])
        candidate_hidden = Metrics.from_summary(hidden_result["summary"])
        accepted, reason = accept_candidate(
            candidate_public=candidate_public,
            candidate_hidden=candidate_hidden,
            champion_public=champion_public,
            champion_hidden=champion_hidden,
        )
        _write_json(
            run_dir / "gate.json",
            {
                "accepted": accepted,
                "reason": reason,
                "public": final_public["summary"],
                "blind": _visible_hidden(hidden_result["summary"]),
                "coverage_floor": coverage_floor,
            },
        )

        if accepted:
            _git("add", "-A")
            _git("commit", "-m", f"harness: cycle {cycle:02d} {change_name}")
            champion_commit = _head()
            if args.push:
                _git("push", "origin", branch)
            state["champion_commit"] = champion_commit
            state["champion_public"] = final_public["summary"]
            state["champion_hidden"] = hidden_result["summary"]
            state["champion_public_failures"] = final_public["failures"]
            decision = "ACCEPTED"
        else:
            _rollback(champion_commit)
            decision = "REJECTED"

        row = {
            "cycle": cycle,
            "hypothesis": plan.get("hypothesis"),
            "change_name": change_name,
            "decision": decision,
            "reason": reason,
            "public_hard_pass_rate": candidate_public.hard_pass_rate,
            "blind_hard_pass_rate": candidate_hidden.hard_pass_rate,
            "index_builds_used": state.get("index_builds_used", 0),
            "review_decision": review.get("decision"),
        }
        _record_history(state_dir, state, row)

    final_hidden = Metrics.from_summary(state["champion_hidden"])
    outcome = "MAX_CYCLES_GOAL_MET" if final_goal_met(hidden=final_hidden, coverage_floor=coverage_floor) else "MAX_CYCLES"
    _write_final_report(state_dir, state, config, outcome)
    return 0 if outcome == "MAX_CYCLES_GOAL_MET" else 2


if __name__ == "__main__":
    raise SystemExit(main())
