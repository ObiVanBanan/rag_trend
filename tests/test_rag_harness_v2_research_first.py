from __future__ import annotations

from harness_rag.research_first_runner import (
    _compact_memory_with_evidence,
    _planner_after_research_prompt,
    _planner_schema_after_research,
    _research_first_prompt,
    _research_schema_with_causal_evidence,
    _result_row_with_evidence,
    _update_ledger_with_mechanism,
    _validator_schema,
)


def test_research_prompt_starts_from_real_tender_corpus_and_causal_history() -> None:
    prompt = _research_first_prompt(
        attempt=2,
        goal="Find useful LD products for tender nomenclature.",
        public_metrics={"hard_pass_rate": 0.83},
        hidden_metrics={"hard_pass_rate": 0.86},
        memory=[
            {
                "cycle": 1,
                "mechanism_family": "candidate_recall",
                "outcome_signature": {
                    "improved": ["public.known_positive_hit_rate"],
                    "regressed": ["hidden.wrong_not_found_rate"],
                },
            }
        ],
        prior_research=[],
    )

    assert "BEFORE the Planner" in prompt
    assert "tender_queries_kontur_5files_review_pool.json.gz" in prompt
    assert "130 unresolved/review cases" in prompt
    assert "33 probable NOT_FOUND" in prompt
    assert "regression guardrails" in prompt.lower()
    assert "NOT a prescribed answer" in prompt
    assert "Start with evidence synthesis, not idea generation" in prompt
    assert "Group experiments by causal mechanism" in prompt
    assert "Repeated similar outcome signatures" in prompt
    assert "high expected information gain" in prompt
    assert "candidate_recall" in prompt


def test_planner_after_research_hides_public_case_failures_and_requires_history_reasoning() -> None:
    prompt = _planner_after_research_prompt(
        cycle=1,
        max_cycles=5,
        goal="Improve real tender matching.",
        research_context="",
        taxonomy="{}",
        memory=[],
        ledger={"mechanism::candidate_recall": {"scientific_evaluations": 2}},
        research_memory=[{"findings": "fresh real-tender research"}],
        public_metrics={"hard_pass_rate": 0.83},
        hidden_metrics={"hard_pass_rate": 0.86},
        scored_failures=[{"id": "BENCHMARK_CASE_MUST_NOT_LEAK", "reason": "x"}],
        unscored_count=0,
        budgets={"research_calls_remaining": 4},
    )

    assert "BENCHMARK_CASE_MUST_NOT_LEAK" not in prompt
    assert "fresh real-tender research" in prompt
    assert "RESEARCH is not an available Planner action" in prompt
    assert "regression guardrails" in prompt
    assert "Reuse the existing `mechanism_family`" in prompt
    assert "repeated similar outcome signatures" in prompt
    assert "information_gain" in prompt


def test_research_first_schemas_keep_pipeline_simple_and_add_causal_metadata() -> None:
    planner = _planner_schema_after_research()
    research = _research_schema_with_causal_evidence()

    assert planner["properties"]["action"]["enum"] == ["IMPLEMENT", "DONE"]
    assert planner["properties"]["history_relation"]["enum"] == [
        "NEW_MECHANISM",
        "NEW_LAYER",
        "REFINEMENT",
        "REPEAT",
        "NONE",
    ]
    assert {"mechanism_family", "history_relation", "information_gain"} <= set(planner["required"])
    assert {"evidence_patterns", "candidate_directions"} <= set(research["required"])
    assert research["properties"]["candidate_directions"]["maxItems"] == 4
    assert _validator_schema()["properties"]["decision"]["enum"] == ["ACCEPT", "REJECT"]


def test_scientific_result_persists_deterministic_metric_evidence() -> None:
    state = {
        "champion_public": {"hard_pass_rate": 0.8, "known_positive_hit_rate": 0.4},
        "champion_hidden": {"hard_pass_rate": 0.8, "wrong_not_found_rate": 0.0},
    }
    active = {
        "cycle": 4,
        "plan": {
            "action": "IMPLEMENT",
            "hypothesis_family": "query_change",
            "mechanism_family": "candidate_recall",
            "history_relation": "REFINEMENT",
            "hypothesis": "demo",
        },
        "champion_public_before": {
            "hard_pass_rate": 0.8,
            "known_positive_hit_rate": 0.4,
            "wrong_not_found_rate": 0.0,
        },
        "champion_hidden_before": {
            "hard_pass_rate": 0.8,
            "wrong_not_found_rate": 0.0,
        },
        "candidate_public": {
            "hard_pass_rate": 0.8,
            "known_positive_hit_rate": 0.5,
            "wrong_not_found_rate": 0.0,
        },
        "candidate_hidden": {
            "hard_pass_rate": 0.8,
            "wrong_not_found_rate": 0.04,
        },
    }

    row = _result_row_with_evidence(
        state=state,
        active=active,
        decision="REJECTED",
        reason="guardrail regression",
        scientifically_evaluated=True,
    )

    assert row["mechanism_family"] == "candidate_recall"
    assert row["history_relation"] == "REFINEMENT"
    assert row["public_metric_delta"]["known_positive_hit_rate"] == 0.1
    assert row["hidden_guardrail_delta"]["wrong_not_found_rate"] == 0.04
    assert "public.known_positive_hit_rate" in row["outcome_signature"]["improved"]
    assert "hidden.wrong_not_found_rate" in row["outcome_signature"]["regressed"]
    assert "hidden.hard_pass_rate" in row["outcome_signature"]["flat"]


def test_compact_memory_preserves_causal_evidence() -> None:
    state = {
        "legacy_memory": [],
        "history": [
            {
                "cycle": 2,
                "family": "query_change",
                "mechanism_family": "candidate_recall",
                "history_relation": "REFINEMENT",
                "hypothesis": "demo",
                "decision": "REJECTED",
                "scientifically_evaluated": True,
                "public_metric_delta": {"known_positive_hit_rate": 0.1},
                "hidden_guardrail_delta": {"wrong_not_found_rate": 0.04},
                "outcome_signature": {
                    "improved": ["public.known_positive_hit_rate"],
                    "regressed": ["hidden.wrong_not_found_rate"],
                    "flat": [],
                },
            }
        ],
    }

    memory = _compact_memory_with_evidence(state)

    assert memory[0]["mechanism_family"] == "candidate_recall"
    assert memory[0]["public_metric_delta"]["known_positive_hit_rate"] == 0.1
    assert memory[0]["outcome_signature"]["regressed"] == ["hidden.wrong_not_found_rate"]


def test_mechanism_ledger_groups_different_hypothesis_families() -> None:
    state = {"hypothesis_ledger": {}}
    first = {
        "family": "canonicalization",
        "mechanism_family": "candidate_recall",
        "history_relation": "NEW_MECHANISM",
        "decision": "REJECTED",
        "scientifically_evaluated": True,
        "outcome_signature": {"improved": ["public.hit"], "regressed": ["hidden.safety"], "flat": []},
    }
    second = {
        "family": "enrichment",
        "mechanism_family": "candidate_recall",
        "history_relation": "REFINEMENT",
        "decision": "REJECTED",
        "scientifically_evaluated": True,
        "outcome_signature": {"improved": ["public.hit"], "regressed": ["hidden.safety"], "flat": []},
    }

    _update_ledger_with_mechanism(state, first)
    _update_ledger_with_mechanism(state, second)

    mechanism = state["hypothesis_ledger"]["mechanism::candidate_recall"]
    assert mechanism["scientific_evaluations"] == 2
    assert mechanism["rejected"] == 2
    assert mechanism["latest_history_relation"] == "REFINEMENT"
    assert list(mechanism["outcome_signature_counts"].values()) == [2]
