from __future__ import annotations

from harness_rag import v2_runner


def test_non_scientific_attempt_does_not_consume_scientific_budget() -> None:
    state = {"cycle": 0, "history": []}
    active = {"cycle": 1, "attempt_id": 1, "plan": {"hypothesis": "inspect domain evidence", "hypothesis_family": "research"}}
    row = {"cycle": 1, "family": "research", "hypothesis": "inspect domain evidence", "scientifically_evaluated": False}

    stamped = v2_runner._stamp_completion(state, row, active)

    assert state["attempts_started"] == 1
    assert state["scientific_iterations"] == 0
    assert stamped["scientific_iteration"] is None
    assert stamped["experiment_id"].startswith("research-")


def test_measured_attempt_consumes_exactly_one_scientific_iteration() -> None:
    state = {"cycle": 2, "attempts_started": 2, "scientific_iterations": 0, "history": []}
    active = {"cycle": 2, "attempt_id": 2, "plan": {"change_name": "v2-cycle-02-demo", "hypothesis": "demo"}}
    row = {"cycle": 2, "family": "query_canonicalization", "hypothesis": "demo", "scientifically_evaluated": True}

    stamped = v2_runner._stamp_completion(state, row, active)

    assert state["attempts_started"] == 2
    assert state["scientific_iterations"] == 1
    assert stamped["scientific_iteration"] == 1
    assert stamped["experiment_id"].startswith("query-canonicalization-")


def test_counter_migration_derives_attempts_and_scientific_iterations() -> None:
    state = {
        "cycle": 3,
        "history": [
            {"cycle": 1, "scientifically_evaluated": False},
            {"cycle": 2, "scientifically_evaluated": True},
            {"cycle": 3, "scientifically_evaluated": False},
        ],
    }

    v2_runner._ensure_execution_counters(state)

    assert state["attempts_started"] == 3
    assert state["scientific_iterations"] == 1


def test_experiment_id_is_stable_for_same_hypothesis_across_attempts() -> None:
    row = {"family": "schema_semantics", "hypothesis": "audit flange subtype evidence"}
    first = v2_runner._experiment_id(row, {"attempt_id": 4})
    second = v2_runner._experiment_id(row, {"attempt_id": 9})

    assert first == second


def test_version_three_legacy_campaign_is_detected_even_after_counter_stamp() -> None:
    state = {
        "version": 3,
        "campaign_id": None,
        "cycle": 17,
        "attempts_started": 17,
        "scientific_iterations": 0,
        "execution_counters_version": 1,
        "usage": {"agent_calls": 0},
        "active": None,
        "history": [
            {"cycle": 3, "decision": "ACCEPTED", "hypothesis": "canonicalize", "reason": "improved"},
            {"cycle": 11, "decision": "IMPLEMENTATION_FAILED", "hypothesis": "OD to DN", "reason": "evaluation failed: qdrant connection refused"},
        ],
    }

    assert v2_runner._looks_like_pre_runner_campaign(state) is True


def test_rehome_legacy_campaign_preserves_champion_and_resets_v2_budget(monkeypatch) -> None:
    monkeypatch.setattr(v2_runner.core, "branch", lambda: "codex/rag-harness-rnd")
    monkeypatch.setattr(v2_runner.core, "head", lambda: "current-head")
    state = {
        "version": 3,
        "campaign_id": None,
        "cycle": 17,
        "champion_commit": "legacy-champion",
        "champion_collection_alias": "steel_active",
        "champion_public": {"hard_pass_rate": 0.8333333333},
        "champion_hidden": {"hard_pass_rate": 0.8666666667},
        "index_builds_used": 2,
        "index_history": [{"count": 1}, {"count": 2}],
        "usage": {"agent_calls": 0},
        "history": [
            {"cycle": 3, "decision": "ACCEPTED", "hypothesis": "canonicalize", "reason": "improved"},
            {"cycle": 11, "decision": "IMPLEMENTATION_FAILED", "hypothesis": "OD to DN", "reason": "evaluation failed: qdrant connection refused"},
        ],
        "push_pending": True,
    }

    migrated = v2_runner._rehome_pre_runner_campaign(state)

    assert migrated["champion_commit"] == "legacy-champion"
    assert migrated["champion_public"]["hard_pass_rate"] == 0.8333333333
    assert migrated["champion_hidden"]["hard_pass_rate"] == 0.8666666667
    assert migrated["index_builds_used"] == 2
    assert migrated["attempts_started"] == 0
    assert migrated["scientific_iterations"] == 0
    assert migrated["cycle"] == 0
    assert migrated["history"] == []
    assert len(migrated["legacy_memory"]) == 2
    assert migrated["hypothesis_ledger"]
    assert migrated["push_pending"] is True
