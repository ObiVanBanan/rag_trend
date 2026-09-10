from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from harness_rag import v2
from harness_rag.runtime import HarnessError


def test_preflight_failure_spends_no_agent_call_or_cycle(monkeypatch, tmp_path: Path) -> None:
    holdout = tmp_path / "hidden.json"
    holdout.write_text("{}", encoding="utf-8")
    state_dir = tmp_path / "state"

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_rag_harness.py",
            "--holdout",
            str(holdout),
            "--state-dir",
            str(state_dir),
            "--max-cycles",
            "1",
        ],
    )
    monkeypatch.setattr(v2, "clean", lambda: True)
    monkeypatch.setattr(v2, "branch", lambda: "codex/rag-harness-rnd")
    monkeypatch.setattr(v2, "head", lambda: "champion")
    monkeypatch.setattr(v2, "ensure_outside_repo", lambda *args, **kwargs: None)

    def fail_preflight(**kwargs):
        raise HarnessError("Qdrant unavailable at http://localhost:6333: connection refused")

    monkeypatch.setattr(v2, "_preflight", fail_preflight)
    monkeypatch.setattr(
        v2,
        "_agent_call",
        lambda **kwargs: pytest.fail("preflight failure must happen before any LLM call"),
    )

    assert v2.main() == v2.TEMP_FAILURE_EXIT

    state = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
    assert state["cycle"] == 0
    assert state["usage"]["agent_calls"] == 0
    preflight = json.loads((state_dir / "preflight.json").read_text(encoding="utf-8"))
    assert preflight["ok"] is False
    assert preflight["error_code"].startswith("INFRA_")


def test_pause_infra_preserves_active_candidate_and_does_not_complete_cycle(
    tmp_path: Path,
) -> None:
    state = {
        "cycle": 0,
        "champion_commit": "champion",
        "active": {"cycle": 1, "stage": "PUBLIC", "candidate_alias": "candidate_collection"},
    }
    active = dict(state["active"])

    result = v2._pause_infra(
        state_dir=tmp_path,
        state=state,
        active=active,
        stage="PUBLIC",
        exc=HarnessError("Qdrant unavailable: connection refused"),
    )

    assert result == v2.TEMP_FAILURE_EXIT
    persisted = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert persisted["cycle"] == 0
    assert persisted["active"]["cycle"] == 1
    assert persisted["active"]["stage"] == "PUBLIC"
    assert persisted["active"]["candidate_alias"] == "candidate_collection"
    assert persisted["active"]["paused_error_code"].startswith("INFRA_")
    assert not (tmp_path / "history_v2.jsonl").exists()


def test_resume_requires_active_stage(monkeypatch, tmp_path: Path) -> None:
    holdout = tmp_path / "hidden.json"
    holdout.write_text("{}", encoding="utf-8")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(
        json.dumps(
            {
                "version": v2.STATE_VERSION,
                "branch": "codex/rag-harness-rnd",
                "champion_commit": "champion",
                "champion_public": {"hard_pass_rate": 0.8},
                "champion_hidden": {"hard_pass_rate": 0.8},
                "cycle": 0,
                "index_builds_used": 0,
                "history": [],
                "legacy_memory": [],
                "hypothesis_ledger": {},
                "research_memory": [],
                "usage": v2._usage_defaults(),
                "active": None,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_rag_harness.py",
            "--holdout",
            str(holdout),
            "--state-dir",
            str(state_dir),
            "--resume",
        ],
    )
    monkeypatch.setattr(v2, "branch", lambda: "codex/rag-harness-rnd")
    monkeypatch.setattr(v2, "head", lambda: "champion")
    monkeypatch.setattr(v2, "ensure_outside_repo", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        v2,
        "_preflight",
        lambda **kwargs: {"ok": True, "qdrant": {"collection": "steel"}},
    )

    with pytest.raises(SystemExit, match="No active v2 cycle to resume"):
        v2.main()
