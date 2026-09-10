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
