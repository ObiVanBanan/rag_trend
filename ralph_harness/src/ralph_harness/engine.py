from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent import role_settings, run_codex_agent
from .evaluator import gate_candidate, metric_delta, run_evaluator
from .prompts import planner_prompt, research_prompt, reviewer_prompt, worker_prompt
from .runtime import (
    HarnessError,
    branch,
    changed_paths,
    clean,
    commit_all,
    git,
    head,
    protected_changes,
    push,
    rollback,
    run,
    write_json,
)
from .schemas import PLANNER_SCHEMA, RESEARCH_SCHEMA, REVIEW_SCHEMA, WORKER_SCHEMA


STATE_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HarnessError(f"expected JSON object in {path}")
    return payload


def _ensure_outside_project(path: Path, project_root: Path) -> None:
    try:
        path.resolve().relative_to(project_root.resolve())
    except ValueError:
        return
    raise HarnessError(f"state directory must live outside the project repository: {path}")


def _state_dir(config: dict[str, Any], project_root: Path) -> Path:
    configured = config.get("state_dir")
    if configured:
        path = Path(str(configured)).expanduser()
        if not path.is_absolute():
            path = project_root / path
        path = path.resolve()
    else:
        project = project_root.name or "project"
        current_branch = branch(project_root).replace("/", "__")
        path = Path.home() / ".ralph-harness" / project / current_branch
    _ensure_outside_project(path, project_root)
    return path


def _evaluation_specs(config: dict[str, Any]) -> list[dict[str, Any]]:
    specs = [dict(item) for item in config.get("evaluators") or []]
    if not specs:
        raise HarnessError("config.evaluators must contain at least one evaluator")
    names = [str(item.get("name") or "") for item in specs]
    if any(not name for name in names) or len(names) != len(set(names)):
        raise HarnessError("evaluator names must be non-empty and unique")
    for item in specs:
        kind = str(item.get("kind") or "gate")
        if kind not in {"gate", "evidence"}:
            raise HarnessError(f"unsupported evaluator kind {kind!r}")
        if not item.get("command"):
            raise HarnessError(f"evaluator {item['name']!r} has no command")
        if kind == "gate" and not item.get("policy"):
            raise HarnessError(f"gate evaluator {item['name']!r} requires a policy")
    return specs


def _evaluation_view(record: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    payload = dict(record.get("payload") or {})
    keys = [str(key) for key in spec.get("review_keys") or []]
    if keys:
        visible_payload = {key: payload.get(key) for key in keys if key in payload}
    else:
        encoded = json.dumps(payload, ensure_ascii=False)
        visible_payload = payload if len(encoded) <= 12000 else {"note": "payload omitted from prompt; inspect evaluator output if needed"}
    return {
        "kind": record.get("kind"),
        "metrics": record.get("metrics"),
        "payload": visible_payload,
    }


def _all_views(evaluations: dict[str, Any], specs: list[dict[str, Any]]) -> dict[str, Any]:
    by_name = {str(spec["name"]): spec for spec in specs}
    return {
        name: _evaluation_view(dict(record), by_name[name])
        for name, record in evaluations.items()
        if name in by_name
    }


def _experiment_id(plan: dict[str, Any]) -> str:
    text = f"{plan.get('experiment_name', '')}\n{plan.get('hypothesis', '')}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _record(
    *,
    state: dict[str, Any],
    state_path: Path,
    active: dict[str, Any],
    decision: str,
    reason: str,
    lesson: str = "",
) -> None:
    plan = dict(active.get("plan") or {})
    row = {
        "cycle": int(active["cycle"]),
        "experiment_id": active.get("experiment_id") or (_experiment_id(plan) if plan else None),
        "experiment_name": plan.get("experiment_name"),
        "hypothesis": plan.get("hypothesis"),
        "decision": decision,
        "reason": reason,
        "lesson": lesson,
        "candidate_deltas": active.get("candidate_deltas") or {},
        "at": _now(),
    }
    state.setdefault("history", []).append(row)
    state["cycle"] = max(int(state.get("cycle") or 0), int(active["cycle"]))
    state["active"] = None
    write_json(state_path, state)


def _agent_call(
    *,
    config: dict[str, Any],
    state: dict[str, Any],
    state_path: Path,
    project_root: Path,
    run_dir: Path,
    role: str,
    prompt: str,
    schema: dict[str, Any],
    sandbox: str,
) -> dict[str, Any]:
    usage = state.setdefault("usage", {"agent_calls": 0, "by_role": {}})
    limit = int(config.get("max_agent_calls", 24))
    if int(usage.get("agent_calls") or 0) >= limit:
        raise HarnessError(f"agent-call budget exhausted ({usage.get('agent_calls')}/{limit})")
    model, effort, network = role_settings(config, role)
    try:
        return run_codex_agent(
            project_root=project_root,
            run_dir=run_dir,
            role=role,
            prompt=prompt,
            schema=schema,
            model=model,
            effort=effort,
            sandbox=sandbox,
            network=network,
        )
    finally:
        usage["agent_calls"] = int(usage.get("agent_calls") or 0) + 1
        by_role = usage.setdefault("by_role", {})
        by_role[role] = int(by_role.get(role) or 0) + 1
        write_json(state_path, state)


def _run_baseline(
    *,
    config: dict[str, Any],
    specs: list[dict[str, Any]],
    state: dict[str, Any],
    state_path: Path,
    project_root: Path,
    state_dir: Path,
) -> None:
    print("\n=== RALPH BASELINE ===", flush=True)
    records: dict[str, Any] = {}
    for spec in specs:
        records[str(spec["name"])] = run_evaluator(
            spec,
            project_root=project_root,
            output_dir=state_dir / "baseline",
            label="champion",
        )
    state["champion_commit"] = head(project_root)
    state["baseline_commit"] = state["champion_commit"]
    state["champion_evaluations"] = records
    state["baseline_evaluations"] = records
    write_json(state_path, state)


def _candidate_eval(
    *,
    config: dict[str, Any],
    specs: list[dict[str, Any]],
    state: dict[str, Any],
    state_path: Path,
    active: dict[str, Any],
    project_root: Path,
    run_dir: Path,
    kind: str,
) -> tuple[bool, list[str]]:
    candidate = dict(active.get("candidate_evaluations") or {})
    deltas = dict(active.get("candidate_deltas") or {})
    reasons: list[str] = []
    for spec in specs:
        if str(spec.get("kind") or "gate") != kind:
            continue
        name = str(spec["name"])
        record = run_evaluator(
            spec,
            project_root=project_root,
            output_dir=run_dir / "eval",
            label="candidate",
        )
        candidate[name] = record
        champion = dict((state.get("champion_evaluations") or {}).get(name) or {})
        deltas[name] = metric_delta(champion, record)
        if kind == "gate":
            ok, gate_reasons, gate_delta = gate_candidate(spec, champion=champion, candidate=record)
            deltas[name] = gate_delta
            if not ok:
                reasons.extend(f"{name}: {reason}" for reason in gate_reasons)
    active["candidate_evaluations"] = candidate
    active["candidate_deltas"] = deltas
    state["active"] = active
    write_json(state_path, state)
    return not reasons, reasons


def _run_tests(config: dict[str, Any], project_root: Path, run_dir: Path) -> tuple[bool, str]:
    command = [str(item) for item in config.get("test_command") or []]
    if not command:
        return True, "tests disabled"
    result = run(
        command,
        root=project_root,
        check=False,
        log_path=run_dir / "tests.log",
    )
    return result.returncode == 0, (result.stdout or "")[-5000:]


def _protected_paths(config: dict[str, Any], config_path: Path, goal_path: Path, project_root: Path) -> list[str]:
    paths = [str(item) for item in config.get("protected_paths") or []]
    for path in (config_path, goal_path):
        try:
            relative = path.resolve().relative_to(project_root.resolve()).as_posix()
        except ValueError:
            continue
        if relative not in paths:
            paths.append(relative)
    return paths


def _execute_active(
    *,
    config: dict[str, Any],
    specs: list[dict[str, Any]],
    state: dict[str, Any],
    state_path: Path,
    state_dir: Path,
    project_root: Path,
    goal: str,
    protected: list[str],
    push_accepted: bool,
) -> str:
    active = dict(state["active"])
    cycle = int(active["cycle"])
    run_dir = state_dir / "runs" / f"{cycle:03d}"
    run_dir.mkdir(parents=True, exist_ok=True)

    while True:
        stage = str(active.get("stage") or "RESEARCH")
        print(f"\n=== CYCLE {cycle} | {stage} ===", flush=True)
        state["active"] = active
        write_json(state_path, state)

        if stage == "RESEARCH":
            result = _agent_call(
                config=config,
                state=state,
                state_path=state_path,
                project_root=project_root,
                run_dir=run_dir,
                role="researcher",
                prompt=research_prompt(
                    goal=goal,
                    champion=_all_views(dict(state["champion_evaluations"]), specs),
                    history=list(state.get("history") or []),
                ),
                schema=RESEARCH_SCHEMA,
                sandbox="read-only",
            )
            active["research"] = result
            active["stage"] = "PLANNER"
            continue

        if stage == "PLANNER":
            plan = _agent_call(
                config=config,
                state=state,
                state_path=state_path,
                project_root=project_root,
                run_dir=run_dir,
                role="planner",
                prompt=planner_prompt(
                    goal=goal,
                    champion=_all_views(dict(state["champion_evaluations"]), specs),
                    history=list(state.get("history") or []),
                    research=dict(active.get("research") or {}),
                ),
                schema=PLANNER_SCHEMA,
                sandbox="read-only",
            )
            active["plan"] = plan
            active["experiment_id"] = _experiment_id(plan)
            if str(plan.get("action")) == "DONE":
                _record(
                    state=state,
                    state_path=state_path,
                    active=active,
                    decision="DONE",
                    reason="planner concluded that no responsible high-value experiment remains",
                    lesson=str((active.get("research") or {}).get("findings") or ""),
                )
                return "DONE"
            active["stage"] = "IMPLEMENTER"
            continue

        if stage == "IMPLEMENTER":
            worker = _agent_call(
                config=config,
                state=state,
                state_path=state_path,
                project_root=project_root,
                run_dir=run_dir,
                role="implementer",
                prompt=worker_prompt(goal=goal, plan=dict(active["plan"]), protected_paths=protected),
                schema=WORKER_SCHEMA,
                sandbox="workspace-write",
            )
            active["worker"] = worker
            if str(worker.get("status")) != "complete":
                rollback(project_root, str(state["champion_commit"]))
                _record(
                    state=state,
                    state_path=state_path,
                    active=active,
                    decision="BLOCKED",
                    reason=str(worker.get("blocker") or "implementer blocked"),
                )
                return "CONTINUE"
            forbidden = protected_changes(project_root, protected)
            if forbidden:
                rollback(project_root, str(state["champion_commit"]))
                _record(
                    state=state,
                    state_path=state_path,
                    active=active,
                    decision="BLOCKED",
                    reason=f"implementer changed protected paths: {forbidden}",
                )
                return "CONTINUE"
            if not changed_paths(project_root):
                _record(
                    state=state,
                    state_path=state_path,
                    active=active,
                    decision="BLOCKED",
                    reason="implementer produced no project changes",
                )
                return "CONTINUE"
            active["stage"] = "TESTS"
            continue

        if stage == "TESTS":
            ok, tail = _run_tests(config, project_root, run_dir)
            if not ok:
                rollback(project_root, str(state["champion_commit"]))
                _record(
                    state=state,
                    state_path=state_path,
                    active=active,
                    decision="IMPLEMENTATION_FAILED",
                    reason=f"tests failed: {tail}",
                )
                return "CONTINUE"
            active["stage"] = "GATES"
            continue

        if stage == "GATES":
            ok, reasons = _candidate_eval(
                config=config,
                specs=specs,
                state=state,
                state_path=state_path,
                active=active,
                project_root=project_root,
                run_dir=run_dir,
                kind="gate",
            )
            if not ok:
                rollback(project_root, str(state["champion_commit"]))
                _record(
                    state=state,
                    state_path=state_path,
                    active=active,
                    decision="REJECTED",
                    reason="; ".join(reasons),
                    lesson="candidate failed deterministic metric guardrails before expensive evidence evaluation",
                )
                return "CONTINUE"
            active["stage"] = "EVIDENCE"
            continue

        if stage == "EVIDENCE":
            _candidate_eval(
                config=config,
                specs=specs,
                state=state,
                state_path=state_path,
                active=active,
                project_root=project_root,
                run_dir=run_dir,
                kind="evidence",
            )
            active["stage"] = "REVIEWER"
            continue

        if stage == "REVIEWER":
            tracked_diff = git(project_root, "diff", "--no-ext-diff", "--").stdout
            paths = sorted(changed_paths(project_root))
            diff = f"CHANGED PATHS:\n{json.dumps(paths, ensure_ascii=False, indent=2)}\n\nTRACKED DIFF:\n{tracked_diff}"
            candidate = dict(active.get("candidate_evaluations") or {})
            review = _agent_call(
                config=config,
                state=state,
                state_path=state_path,
                project_root=project_root,
                run_dir=run_dir,
                role="reviewer",
                prompt=reviewer_prompt(
                    goal=goal,
                    plan=dict(active["plan"]),
                    diff=diff,
                    champion=_all_views(dict(state["champion_evaluations"]), specs),
                    candidate=_all_views(candidate, specs),
                    deltas=dict(active.get("candidate_deltas") or {}),
                ),
                schema=REVIEW_SCHEMA,
                sandbox="read-only",
            )
            active["review"] = review
            if str(review.get("decision")) != "ACCEPT":
                rollback(project_root, str(state["champion_commit"]))
                _record(
                    state=state,
                    state_path=state_path,
                    active=active,
                    decision="REJECTED",
                    reason=str(review.get("summary") or "reviewer rejected candidate"),
                    lesson=str(review.get("lesson") or review.get("next_direction") or ""),
                )
                return "CONTINUE"

            experiment_name = str((active.get("plan") or {}).get("experiment_name") or "experiment")
            new_commit = commit_all(project_root, f"ralph: cycle {cycle:02d} {experiment_name}")
            if push_accepted:
                state["push_pending"] = not push(project_root)
            state["champion_commit"] = new_commit
            state["champion_evaluations"] = candidate
            _record(
                state=state,
                state_path=state_path,
                active=active,
                decision="ACCEPTED",
                reason=str(review.get("summary") or "candidate accepted"),
                lesson=str(review.get("lesson") or review.get("next_direction") or ""),
            )
            print(f"\n+++ CYCLE {cycle} ACCEPTED | champion={new_commit[:12]} +++", flush=True)
            return "CONTINUE"

        raise HarnessError(f"unknown stage {stage!r}")


def _write_report(state_dir: Path, state: dict[str, Any], specs: list[dict[str, Any]]) -> None:
    lines = [
        "# Ralph Experiment Harness Report",
        "",
        f"- Champion: `{state.get('champion_commit', '')}`",
        f"- Cycles completed: **{state.get('cycle', 0)}**",
        f"- Agent calls: **{(state.get('usage') or {}).get('agent_calls', 0)}**",
        "",
        "## Champion evaluations",
        "",
        "```json",
        json.dumps(_all_views(dict(state.get("champion_evaluations") or {}), specs), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Experiment history",
        "",
    ]
    for row in state.get("history") or []:
        lines.append(
            f"- Cycle {row.get('cycle')}: **{row.get('decision')}** — {row.get('experiment_name') or ''}: "
            f"{row.get('hypothesis') or ''} | {row.get('lesson') or row.get('reason') or ''}"
        )
    (state_dir / "FINAL_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_loop(
    *,
    config_path: Path,
    project_root: Path,
    resume: bool = False,
    fresh: bool = False,
    push_accepted: bool = False,
    max_cycles_override: int | None = None,
) -> int:
    project_root = project_root.resolve()
    config_path = config_path.resolve()
    config = _read_json(config_path)
    if int(config.get("version") or 1) != 1:
        raise HarnessError("unsupported config version")
    specs = _evaluation_specs(config)
    max_cycles = int(max_cycles_override or config.get("max_cycles") or 5)
    if max_cycles < 1:
        raise HarnessError("max_cycles must be positive")

    current_branch = branch(project_root)
    if current_branch in {"main", "master"} and not bool(config.get("allow_main_branch", False)):
        raise HarnessError("refusing autonomous edits on main/master; use a work branch")

    state_dir = _state_dir(config, project_root)
    if fresh and state_dir.exists():
        shutil.rmtree(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "state.json"

    if state_path.exists():
        state = _read_json(state_path)
    else:
        state = {
            "version": STATE_VERSION,
            "project_root": str(project_root),
            "branch": current_branch,
            "baseline_commit": head(project_root),
            "champion_commit": head(project_root),
            "cycle": 0,
            "champion_evaluations": {},
            "baseline_evaluations": {},
            "history": [],
            "usage": {"agent_calls": 0, "by_role": {}},
            "active": None,
        }
        write_json(state_path, state)

    if state.get("branch") != current_branch:
        raise HarnessError(f"state belongs to branch {state.get('branch')!r}, current branch is {current_branch!r}")
    if state.get("active"):
        if not resume:
            raise HarnessError("an interrupted experiment exists; rerun with --resume")
    elif resume:
        raise HarnessError("there is no active experiment to resume")
    elif not clean(project_root):
        raise HarnessError("working tree must be clean before starting a new experiment")
    elif state.get("champion_commit") and head(project_root) != state.get("champion_commit"):
        raise HarnessError(
            "HEAD differs from saved champion. Use --fresh to establish a new campaign baseline, or restore the saved champion."
        )

    goal_setting = str(config.get("goal_file") or "RALPH_GOAL.md")
    goal_path = Path(goal_setting)
    if not goal_path.is_absolute():
        goal_path = project_root / goal_path
    if not goal_path.exists():
        raise HarnessError(f"goal file not found: {goal_path}")
    goal = goal_path.read_text(encoding="utf-8").strip()
    protected = _protected_paths(config, config_path, goal_path, project_root)

    if not state.get("champion_evaluations"):
        _run_baseline(
            config=config,
            specs=specs,
            state=state,
            state_path=state_path,
            project_root=project_root,
            state_dir=state_dir,
        )

    if resume:
        outcome = _execute_active(
            config=config,
            specs=specs,
            state=state,
            state_path=state_path,
            state_dir=state_dir,
            project_root=project_root,
            goal=goal,
            protected=protected,
            push_accepted=push_accepted,
        )
        state = _read_json(state_path)
        if outcome == "DONE":
            _write_report(state_dir, state, specs)
            return 0

    while int(state.get("cycle") or 0) < max_cycles:
        if int((state.get("usage") or {}).get("agent_calls") or 0) >= int(config.get("max_agent_calls", 24)):
            print("Agent-call budget exhausted; stopping campaign.", flush=True)
            break
        next_cycle = int(state.get("cycle") or 0) + 1
        state["active"] = {"cycle": next_cycle, "stage": "RESEARCH", "started_at": _now()}
        write_json(state_path, state)
        outcome = _execute_active(
            config=config,
            specs=specs,
            state=state,
            state_path=state_path,
            state_dir=state_dir,
            project_root=project_root,
            goal=goal,
            protected=protected,
            push_accepted=push_accepted,
        )
        state = _read_json(state_path)
        if outcome == "DONE":
            break

    _write_report(state_dir, state, specs)
    print(f"\nReport: {state_dir / 'FINAL_REPORT.md'}", flush=True)
    return 0
