from __future__ import annotations

import json
import sys
from pathlib import Path

from harness_rag import optimization_runner, v2_runner


def test_resume_closes_candidate_already_removed_by_partial_rollback(monkeypatch, tmp_path: Path) -> None:
    state = {
        "champion_commit": "champion",
        "champion_public": {"hard_pass_rate": 0.9, "hard_gate_cases": 30},
        "champion_hidden": {"hard_pass_rate": 0.9, "hard_gate_cases": 30},
        "attempts_started": 1,
        "scientific_iterations": 0,
        "history": [],
        "active": {
            "cycle": 1,
            "attempt_id": 1,
            "stage": "TESTS",
            "action": "IMPLEMENT",
            "plan": {
                "action": "IMPLEMENT",
                "hypothesis_family": "retrieval",
                "hypothesis": "increase candidate recall",
            },
        },
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    holdout = tmp_path / "hidden.json"
    holdout.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["run_rag_harness.py", "--holdout", str(holdout), "--resume"],
    )
    monkeypatch.setattr(optimization_runner.core, "branch", lambda: "codex/test")
    monkeypatch.setattr(optimization_runner.core, "_state_dir", lambda args, current_branch: tmp_path)
    monkeypatch.setattr(optimization_runner.core, "changed_paths", lambda: set())

    recorded: dict[str, object] = {}

    def fake_record(*, state_dir: Path, state: dict, row: dict) -> None:
        recorded.update(row)
        state["active"] = None

    monkeypatch.setattr(v2_runner, "_record_cycle_scientific", fake_record)

    assert optimization_runner._recover_already_rolled_back_resume() is True
    assert recorded["decision"] == "IMPLEMENTATION_FAILED"
    assert recorded["scientifically_evaluated"] is False
    assert recorded["error_code"] == "ROLLED_BACK_BEFORE_STATE_COMMIT"
    assert "--resume" not in sys.argv


def test_resume_preserves_live_candidate_with_changes(monkeypatch, tmp_path: Path) -> None:
    state = {
        "active": {"cycle": 1, "attempt_id": 1, "stage": "TESTS"},
    }
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    holdout = tmp_path / "hidden.json"
    holdout.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(
        sys,
        "argv",
        ["run_rag_harness.py", "--holdout", str(holdout), "--resume"],
    )
    monkeypatch.setattr(optimization_runner.core, "branch", lambda: "codex/test")
    monkeypatch.setattr(optimization_runner.core, "_state_dir", lambda args, current_branch: tmp_path)
    monkeypatch.setattr(optimization_runner.core, "changed_paths", lambda: {"src/example.py"})

    assert optimization_runner._recover_already_rolled_back_resume() is False
    assert "--resume" in sys.argv
