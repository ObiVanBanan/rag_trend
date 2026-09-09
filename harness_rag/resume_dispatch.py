from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from . import resume, supervisor

orchestrator = supervisor.orchestrator


def _commit_paths(commit: str) -> list[str]:
    result = orchestrator.git(
        "diff-tree", "--no-commit-id", "--name-only", "-r", commit, check=False
    )
    if result.returncode != 0:
        raise orchestrator.HarnessError(f"Cannot inspect commit {commit}.")
    return [
        line.strip().replace("\\", "/")
        for line in result.stdout.splitlines()
        if line.strip()
    ]


def _find_candidate_commit(saved_champion: str, current_head: str, expected_message: str) -> str | None:
    result = orchestrator.git(
        "log", "--format=%H%x09%s", f"{saved_champion}..{current_head}", check=False
    )
    if result.returncode != 0:
        return None

    commits: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if "\t" not in line:
            continue
        sha, subject = line.split("\t", 1)
        commits.append((sha.strip(), subject.strip()))

    matches = [sha for sha, subject in commits if subject == expected_message]
    if len(matches) != 1:
        return None
    candidate_commit = matches[0]

    # Any additional commits pulled/rebased around the candidate must be harness-only.
    for sha, _subject in commits:
        if sha == candidate_commit:
            continue
        paths = _commit_paths(sha)
        if not paths or not all(resume._harness_only_path(path) for path in paths):
            raise orchestrator.HarnessError(
                "Cannot recover post-push state because non-harness commits were added "
                f"around the accepted candidate: {sha} -> {paths}"
            )
    return candidate_commit


def _load_final_public(run_dir: Path) -> dict[str, Any]:
    for name in ("public_after_fixer.json", "public_after_worker.json"):
        path = run_dir / name
        if path.exists():
            return resume._load_public_result(path)
    raise orchestrator.HarnessError(
        "Accepted gate exists but no public_after_fixer/public_after_worker result can be recovered."
    )


def _already_recorded(state: dict[str, Any], cycle: int) -> bool:
    return any(
        int(row.get("cycle", -1)) == cycle and row.get("decision") == "ACCEPTED"
        for row in list(state.get("history") or [])
        if isinstance(row, dict)
    )


def _recover_post_commit_push_failure(args: Any) -> int | None:
    current_branch = orchestrator.branch()
    state_dir = resume._state_dir(args, current_branch)
    state_path = state_dir / "state.json"
    if not state_path.exists():
        return None

    state = resume._read_json(state_path)
    cycle = int(state.get("cycle", 0))
    if cycle <= 0:
        return None

    run_dir = state_dir / "runs" / f"{cycle:03d}"
    gate_path = run_dir / "gate.json"
    plan_path = run_dir / "plan.json"
    if not gate_path.exists() or not plan_path.exists():
        return None

    gate = resume._read_json(gate_path)
    if gate.get("accepted") is not True:
        return None

    plan = resume._read_json(plan_path)
    change_name = str(plan.get("change_name") or "")
    if not change_name:
        return None

    saved_champion = str(state.get("champion_commit") or "")
    current_head = orchestrator.head()
    if not saved_champion or current_head == saved_champion:
        return None

    expected_message = f"harness: cycle {cycle:02d} {change_name}"
    candidate_commit = _find_candidate_commit(saved_champion, current_head, expected_message)
    if candidate_commit is None:
        return None

    if not orchestrator.clean():
        raise orchestrator.HarnessError(
            "Accepted candidate commit was found, but the worktree is dirty. "
            "Commit/stash unrelated work before resuming post-push recovery."
        )

    final_public = _load_final_public(run_dir)
    public_summary = dict(gate.get("public") or final_public["summary"])
    blind_summary = dict(gate.get("blind") or {})
    if not blind_summary:
        raise orchestrator.HarnessError("Accepted gate has no recoverable blind summary.")

    review_decision = None
    review_path = run_dir / "review.json"
    if review_path.exists():
        review_decision = str(resume._read_json(review_path).get("decision") or "")

    # The candidate was already measured and committed locally. The crash happened
    # before the ordinary loop copied those accepted metrics into state.json.
    state["champion_commit"] = current_head
    state["champion_collection_alias"] = gate.get("candidate_collection_alias") or None
    state["champion_public"] = public_summary
    state["champion_hidden"] = blind_summary
    state["champion_public_failures"] = list(final_public.get("failures") or [])

    if not _already_recorded(state, cycle):
        candidate_public = orchestrator.Metrics.from_summary(public_summary)
        candidate_hidden = orchestrator.Metrics.from_summary(blind_summary)
        orchestrator._record(
            state_dir,
            state,
            orchestrator._row(
                cycle=cycle,
                plan=plan,
                decision="ACCEPTED",
                reason=str(gate.get("reason") or "candidate accepted before push failure"),
                public_rate=candidate_public.hard_pass_rate,
                blind_rate=candidate_hidden.hard_pass_rate,
                state=state,
                review_decision=review_decision,
                candidate_alias=gate.get("candidate_collection_alias") or None,
            ),
        )
    else:
        orchestrator.write_json(state_path, state)

    print(
        f"\n=== RECOVERED CYCLE {cycle} AFTER PUSH FAILURE ===\n"
        f"Candidate commit: {candidate_commit[:12]}\n"
        f"Current HEAD:      {current_head[:12]}\n"
        f"Public hard-pass:  {public_summary.get('hard_pass_rate')}\n"
        f"Blind hard-pass:   {blind_summary.get('hard_pass_rate')}\n"
        "The accepted champion is restored in state; no evaluation or agent stage is repeated.",
        flush=True,
    )

    if args.push:
        push = orchestrator.git("push", "origin", current_branch, check=False)
        if push.returncode != 0:
            state["push_pending"] = True
            orchestrator.write_json(state_path, state)
            print(
                "WARNING: GitHub push is still unavailable. Continuing locally with push_pending=true.",
                flush=True,
            )
        else:
            state.pop("push_pending", None)
            orchestrator.write_json(state_path, state)

    # Resume means: recover the interrupted accepted cycle, then continue with N+1.
    sys.argv = [arg for arg in sys.argv if arg not in {"--resume", "--fresh"}]
    return supervisor.main()


def main() -> int:
    args = resume._parser().parse_args()
    if not args.resume:
        raise SystemExit("Use --resume with the resume entrypoint.")
    if args.fresh:
        raise SystemExit("--resume and --fresh are mutually exclusive.")

    recovered = _recover_post_commit_push_failure(args)
    if recovered is not None:
        return recovered
    return resume.main()
