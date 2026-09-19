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



def test_candidate_snapshot_requires_non_test_non_openspec_causal_path(monkeypatch) -> None:
    monkeypatch.setattr(
        v2,
        "changed_paths",
        lambda: {
            "openspec/changes/v2-cycle-10-demo/proposal.md",
            "tests/test_demo.py",
        },
    )
    active = {"plan": {"change_name": "v2-cycle-10-demo"}}

    paths, causal = v2._candidate_path_snapshot(active)

    assert paths == [
        "openspec/changes/v2-cycle-10-demo/proposal.md",
        "tests/test_demo.py",
    ]
    assert causal == []


def test_candidate_snapshot_keeps_runtime_product_change_as_causal(monkeypatch) -> None:
    monkeypatch.setattr(
        v2,
        "changed_paths",
        lambda: {
            "openspec/changes/v2-cycle-10-demo/proposal.md",
            "tests/test_demo.py",
            "src/nomenclature_matcher/matcher.py",
        },
    )
    active = {"plan": {"change_name": "v2-cycle-10-demo"}}

    _, causal = v2._candidate_path_snapshot(active)

    assert causal == ["src/nomenclature_matcher/matcher.py"]


def test_promotion_paths_reject_unexpected_late_file(monkeypatch) -> None:
    active = {
        "candidate_paths": [
            "openspec/changes/v2-cycle-10-demo/proposal.md",
            "src/nomenclature_matcher/matcher.py",
        ]
    }
    monkeypatch.setattr(
        v2,
        "changed_paths",
        lambda: {
            "openspec/changes/v2-cycle-10-demo/proposal.md",
            "src/nomenclature_matcher/matcher.py",
            "openspec/changes/v2-cycle-11-stale/proposal.md",
        },
    )

    try:
        v2._promotion_paths(active)
    except v2.HarnessError as exc:
        assert "unexpected files appeared" in str(exc)
        assert "v2-cycle-11-stale" in str(exc)
    else:
        raise AssertionError("unexpected late file must block promotion")


def test_implementer_no_causal_diff_stops_before_tests_or_eval(monkeypatch, tmp_path: Path) -> None:
    config = v2._read_json(v2.CONFIG_PATH)
    active = {
        "cycle": 10,
        "attempt_id": 10,
        "stage": "IMPLEMENTER",
        "plan": {
            "action": "IMPLEMENT",
            "change_name": "v2-cycle-10-demo",
            "hypothesis_family": "demo",
            "hypothesis": "test a no-op implementation",
        },
        "planner_paths": ["openspec/changes/v2-cycle-10-demo/proposal.md"],
    }
    state = {
        "champion_commit": "champion",
        "champion_collection_alias": None,
        "champion_public": {"hard_pass_rate": 0.7, "hard_gate_cases": 30},
        "champion_hidden": {"hard_pass_rate": 0.7, "hard_gate_cases": 30},
        "active": active,
    }

    monkeypatch.setattr(v2, "_agent_call", lambda **kwargs: {"status": "complete", "needs_reindex": False})
    monkeypatch.setattr(v2, "protected_changes", lambda: [])
    monkeypatch.setattr(
        v2,
        "changed_paths",
        lambda: {
            "openspec/changes/v2-cycle-10-demo/proposal.md",
            "tests/test_demo.py",
        },
    )
    monkeypatch.setattr(
        v2,
        "run_tests",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("tests must not run")),
    )

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
    assert recorded["decision"] == "IMPLEMENTATION_FAILED"
    assert recorded["scientifically_evaluated"] is False
    assert recorded["error_code"] == "NO_CAUSAL_DIFF"
    assert state["active"] is None
