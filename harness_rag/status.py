from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .runtime import branch


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _state_dir(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    return Path.home() / ".rag-trend-harness" / branch().replace("/", "__")


def _rate(summary: dict[str, Any]) -> Any:
    return summary.get("hard_pass_rate")


def _latest_safe_hidden_summary(run_dir: Path) -> dict[str, Any]:
    candidates = sorted(run_dir.glob("hidden*_summary.json"))
    if not candidates:
        return {}
    payload = _read_json(candidates[-1])
    return dict(payload.get("summary") or {})


def _legacy_campaign_pending(state: dict[str, Any]) -> bool:
    history = [row for row in state.get("history") or [] if isinstance(row, dict)]
    if not history or state.get("campaign_id") or state.get("active"):
        return False
    usage = dict(state.get("usage") or {})
    if int(usage.get("agent_calls") or 0) > 0:
        return False
    return all(
        row.get("attempt_id") is None
        and row.get("experiment_id") is None
        and row.get("scientific_iteration") is None
        for row in history
    )


def _scientific_iterations(state: dict[str, Any]) -> int:
    if _legacy_campaign_pending(state):
        return 0
    if "scientific_iterations" in state:
        return int(state.get("scientific_iterations") or 0)
    return sum(1 for row in state.get("history") or [] if bool(row.get("scientifically_evaluated")))


def _attempts_started(state: dict[str, Any]) -> int:
    if _legacy_campaign_pending(state):
        return 0
    if "attempts_started" in state:
        return int(state.get("attempts_started") or 0)
    return int(state.get("cycle") or 0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Show compact Harness v2 state, scores and model budget usage.")
    parser.add_argument("--state-dir", default=None)
    args = parser.parse_args()

    root = _state_dir(args.state_dir)
    state = _read_json(root / "state.json")
    if not state:
        raise SystemExit(f"Harness state not found: {root / 'state.json'}")

    public = dict(state.get("champion_public") or {})
    hidden = dict(state.get("champion_hidden") or {})
    usage = dict(state.get("usage") or {})
    active = dict(state.get("active") or {})
    legacy_pending = _legacy_campaign_pending(state)

    print("=== HARNESS V2 ===")
    print("state:", root)
    print("campaign:", state.get("campaign_id"))
    print("attempts started:", _attempts_started(state))
    print("scientific iterations:", _scientific_iterations(state))
    if legacy_pending:
        print("legacy campaign rows awaiting automatic rehome:", len(state.get("history") or []))
    print("champion:", state.get("champion_commit"))
    print("public hard-pass:", _rate(public))
    print("hidden-validation hard-pass:", _rate(hidden))
    print("index builds used:", state.get("index_builds_used"))
    print("push pending:", bool(state.get("push_pending", False)))

    print("\n=== USAGE ===")
    for key in (
        "agent_calls",
        "planner_calls",
        "research_calls",
        "implementer_calls",
        "reviewer_calls",
        "fixer_calls",
        "reported_tokens",
        "elapsed_seconds",
    ):
        print(f"{key}: {usage.get(key, 0)}")
    if usage.get("by_model"):
        print("by_model:")
        for model, row in sorted(dict(usage["by_model"]).items()):
            print(f"  {model}: calls={row.get('calls')} elapsed={row.get('elapsed_seconds')}s")

    if active:
        print("\n=== ACTIVE / PAUSED ===")
        print("attempt:", active.get("attempt_id", active.get("cycle")))
        print("experiment:", active.get("experiment_id"))
        print("stage:", active.get("stage"))
        print("action:", active.get("action"))
        print("error:", active.get("paused_error_code"))
        print("reason:", active.get("paused_reason"))
        run_dir = root / "runs" / f"{int(active.get('cycle', 0)):03d}"
        public_candidate = dict(active.get("candidate_public") or {})
        hidden_candidate = dict(active.get("candidate_hidden") or {}) or _latest_safe_hidden_summary(run_dir)
        if public_candidate:
            print("candidate public hard-pass:", _rate(public_candidate))
        if hidden_candidate:
            print("candidate hidden hard-pass:", _rate(hidden_candidate))

    print("\n=== CURRENT CAMPAIGN HISTORY ===")
    history = [] if legacy_pending else list(state.get("history") or [])
    if not history:
        print("(no completed v2 attempts yet)")
    for row in history:
        attempt = row.get("attempt_id", row.get("cycle"))
        science = row.get("scientific_iteration")
        decision = row.get("decision")
        family = row.get("family")
        pub = row.get("public_hard_pass_rate")
        hid = row.get("hidden_validation_hard_pass_rate")
        dp = row.get("public_delta_cases")
        dh = row.get("hidden_delta_cases")
        code = row.get("error_code")
        print(
            f"attempt {attempt}: {decision} science={science} family={family} public={pub} hidden={hid} "
            f"delta_cases=({dp},{dh}) error={code} experiment={row.get('experiment_id')}"
        )
        if row.get("hypothesis"):
            print("  hypothesis:", row.get("hypothesis"))
        if row.get("reason"):
            print("  reason:", row.get("reason"))

    print("\n=== HYPOTHESIS LEDGER ===")
    ledger = dict(state.get("hypothesis_ledger") or {})
    if not ledger and legacy_pending:
        print("(will be rebuilt from legacy campaign on next harness run)")
    elif not ledger:
        print("(empty)")
    for family, row in sorted(ledger.items()):
        print(
            f"{family}: attempts={row.get('attempts', 0)} "
            f"scientific={row.get('scientific_evaluations', 0)} "
            f"accepted={row.get('accepted', 0)} rejected={row.get('rejected', 0)} "
            f"not_evaluated={row.get('not_evaluated', 0)}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
