from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from harness_rag import v2


def test_persisted_reviewer_reject_is_applied_without_second_agent_call(
    monkeypatch, tmp_path: Path
) -> None:
    config = v2._read_json(v2.CONFIG_PATH)
    state = {
        "champion_commit": "champion",
        "champion_collection_alias": None,
        "champion_public": {"hard_pass_rate": 0.6, "hard_gate_cases": 30},
        "champion_hidden": {"hard_pass_rate": 0.6, "hard_gate_cases": 30},
        "active": {
            "cycle": 6,
            "attempt_id": 6,
            "stage": "REVIEWER",
            "plan": {
                "action": "IMPLEMENT",
                "hypothesis_family": "constraint-aware candidate recall",
                "hypothesis": "recover compatible candidates",
            },
            "candidate_public": {"hard_pass_rate": 0.7, "hard_gate_cases": 30},
            "candidate_hidden": {"hard_pass_rate": 0.7, "hard_gate_cases": 30},
            "review": {
                "decision": "REJECT",
                "summary": "candidate has unexplained regressions",
                "next_direction": "isolate the mechanism",
            },
        },
    }

    def fail_agent_call(**kwargs):
        raise AssertionError("reviewer must not be called again after verdict persistence")

    monkeypatch.setattr(v2, "_agent_call", fail_agent_call)

    def persist(state: dict, state_path: Path, active: dict) -> None:
        state["active"] = dict(active)

    monkeypatch.setattr(v2, "_persist_active", persist)
    recorded: dict[str, object] = {}

    def rollback_and_record(**kwargs) -> None:
        recorded.update(kwargs)
        kwargs["state"]["active"] = None

    monkeypatch.setattr(v2, "_rollback_and_record", rollback_and_record)

    result = v2._execute_active(
        args=SimpleNamespace(push=False),
        config=config,
        state_dir=tmp_path,
        state=state,
        holdout=tmp_path / "unused-hidden.json",
    )

    assert result is None
    assert recorded["decision"] == "REJECTED"
    assert recorded["scientifically_evaluated"] is True
    assert recorded["error_code"] == "MECHANISM_REJECT"
    assert state["active"] is None


def test_repeated_blocked_mechanism_is_suppressed() -> None:
    state = {
        "history": [
            {
                "mechanism_family": "catalog_feasibility_provenance",
                "decision": "BLOCKED",
                "scientifically_evaluated": False,
                "error_code": "IMPLEMENTER_BLOCKED",
            },
            {
                "mechanism_family": "catalog_feasibility_provenance",
                "decision": "BLOCKED",
                "scientifically_evaluated": False,
                "error_code": "IMPLEMENTER_BLOCKED",
            },
            {
                "mechanism_family": "other",
                "decision": "BLOCKED",
                "scientifically_evaluated": False,
                "error_code": "IMPLEMENTER_BLOCKED",
            },
        ]
    }

    assert v2._suppressed_mechanisms(state, threshold=2) == {
        "catalog_feasibility_provenance": 2
    }
