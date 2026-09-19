from __future__ import annotations

import sys

from . import v2 as core
from . import v2_runner
from .current_dataset_runner import install_current_dataset_optimization, restore_candidate_current_dataset_evidence
from .diagnostic_runner import _accept_candidate, _public_precheck, _rollback_and_record_diagnostic
from .provenance_runner import install_campaign_provenance
from .research_first_runner import install_research_first
from .runtime import harness_process_lock


# Harness/CI-only files may change without invalidating a saved product champion.
# This lets an existing external state safely adopt the upgraded runner instead of
# requiring --fresh just because the harness workflow itself changed.
core.HARNESS_ONLY_EXACT.add(".github/workflows/test-competitor-lookup.yml")
core.HARNESS_ONLY_EXACT.add(".gitignore")

_ORIGINAL_ADOPT_HARNESS_ONLY_HEAD = core._adopt_harness_only_head

_CANDIDATE_STAGES = {
    "TESTS",
    "TESTS_AFTER_FIX",
    "PUBLIC",
    "PUBLIC_AFTER_FIX",
    "HIDDEN",
    "HIDDEN_AFTER_FIX",
    "REVIEWER",
    "APPLY_REVIEW",
    "FIXER",
}


def _adopt_harness_only_head_with_current_dataset(state: dict, state_path) -> None:
    previous_commit = str(state.get("champion_commit") or "")
    current_record = state.get("champion_current_dataset")
    _ORIGINAL_ADOPT_HARNESS_ONLY_HEAD(state, state_path)
    current_commit = str(state.get("champion_commit") or "")
    if (
        current_commit
        and current_commit != previous_commit
        and isinstance(current_record, dict)
        and current_record.get("commit") == previous_commit
    ):
        promoted_record = dict(current_record)
        promoted_record["commit"] = current_commit
        state["champion_current_dataset"] = promoted_record
        repeatability = state.get("champion_repeatability")
        if (
            isinstance(repeatability, dict)
            and repeatability.get("commit") == previous_commit
        ):
            repeatability = dict(repeatability)
            repeatability["commit"] = current_commit
            state["champion_repeatability"] = repeatability
        core.write_json(state_path, state)
        print(
            f"Retained 783 champion baseline across harness-only HEAD adoption "
            f"{previous_commit[:12]} -> {current_commit[:12]}.",
            flush=True,
        )


def _recover_already_rolled_back_resume() -> bool:
    """Close an interrupted candidate if rollback already removed its worktree.

    A previous process can die after ``git reset --hard`` but before state.json is
    updated (for example when ``git clean`` hits a locked Windows pytest temp
    directory). Resuming such a TESTS/PUBLIC/etc. stage would evaluate the
    champion as if it were still the candidate. Detect that impossible state,
    record the attempt as a non-scientific implementation failure, and continue
    with a fresh attempt while preserving the established baselines.
    """
    if "--resume" not in sys.argv:
        return False

    args = core._parser().parse_args()
    state_dir = core._state_dir(args, core.branch())
    state_path = state_dir / "state.json"
    if not state_path.exists():
        return False

    state = core._read_json(state_path)
    active = dict(state.get("active") or {})
    stage = str(active.get("stage") or "")

    # A persisted reviewer verdict is already a completed scientific stage.
    # Let core resume at APPLY_REVIEW instead of classifying a clean worktree as
    # an implementation failure or spending another reviewer/783 evaluation.
    review = active.get("review")
    if (
        stage in {"REVIEWER", "APPLY_REVIEW"}
        and isinstance(review, dict)
        and review.get("decision")
    ):
        active = restore_candidate_current_dataset_evidence(
            state=state,
            state_path=state_path,
            state_dir=state_dir,
            active=active,
        )
        state["active"] = active
        core.write_json(state_path, state)
        return False

    # Planner output is persisted before its OpenSpec scope is validated. If the
    # planner violated scope and rollback already reset the worktree, an older
    # process may die before recording BLOCKED. Close that attempt instead of
    # spending another planner call on resume.
    if stage == "PLANNER" and isinstance(active.get("plan"), dict) and not core.changed_paths():
        plan = dict(active.get("plan") or {})
        print(
            f"Recovered rolled-back planner attempt {active.get('attempt_id', active.get('cycle'))}; "
            "recording prior scope/contract failure without another planner call.",
            flush=True,
        )
        row = core._result_row(
            state=state,
            active=active,
            decision="BLOCKED",
            reason="planner output had already been rolled back before BLOCKED state persistence completed",
            scientifically_evaluated=False,
            error_code="PLANNER_SCOPE",
            lesson=(
                "Planner must keep planning writes inside one fresh openspec/changes cycle directory; "
                "product-layer edits belong to the implementer."
            ),
        )
        v2_runner._record_cycle_scientific(state_dir=state_dir, state=state, row=row)
        sys.argv = [arg for arg in sys.argv if arg != "--resume"]
        return True

    if stage not in _CANDIDATE_STAGES:
        return False
    if core.changed_paths():
        return False

    print(
        f"Recovered rolled-back active attempt {active.get('attempt_id', active.get('cycle'))} "
        f"from stage={stage}; candidate worktree is already gone.",
        flush=True,
    )
    row = core._result_row(
        state=state,
        active=active,
        decision="IMPLEMENTATION_FAILED",
        reason="candidate had already been rolled back before state persistence completed",
        scientifically_evaluated=False,
        error_code="ROLLED_BACK_BEFORE_STATE_COMMIT",
        lesson="Infrastructure rollback failed after reset; candidate code was not scientifically evaluated.",
    )
    v2_runner._record_cycle_scientific(state_dir=state_dir, state=state, row=row)
    sys.argv = [arg for arg in sys.argv if arg != "--resume"]
    return True


def main() -> int:
    # Preserve the existing research-first/provenance/metric guardrail stack and
    # add the current 783-row corpus as the primary optimization evidence.
    install_research_first()
    install_campaign_provenance()
    install_current_dataset_optimization()
    core._public_precheck = _public_precheck
    core.accept_candidate = _accept_candidate
    core._rollback_and_record = _rollback_and_record_diagnostic
    core._adopt_harness_only_head = _adopt_harness_only_head_with_current_dataset

    args = core._parser().parse_args()
    current_branch = core.branch()
    state_dir = core._state_dir(args, current_branch)
    core.ensure_outside_repo(state_dir, "Harness state directory")
    try:
        with harness_process_lock(state_dir):
            # Recovery mutates state/worktree and therefore must happen under the
            # same repository lock as the main runner.
            _recover_already_rolled_back_resume()
            return v2_runner.main()
    except core.HarnessError as exc:
        if str(exc).startswith("HARNESS_ALREADY_RUNNING:"):
            print("\n=== HARNESS_ALREADY_RUNNING ===", flush=True)
            print(str(exc), flush=True)
            print(
                "Stop the other harness process (or let it finish) before starting/resuming this worktree.",
                flush=True,
            )
            return 73
        raise
