from __future__ import annotations

from harness_rag.research_first_runner import (
    _planner_after_research_prompt,
    _planner_schema_after_research,
    _research_first_prompt,
    _validator_schema,
)


def test_research_prompt_starts_from_real_tender_corpus() -> None:
    prompt = _research_first_prompt(
        attempt=2,
        goal="Find useful LD products for tender nomenclature.",
        public_metrics={"hard_pass_rate": 0.83},
        hidden_metrics={"hard_pass_rate": 0.86},
        memory=[],
        prior_research=[],
    )

    assert "BEFORE the Planner" in prompt
    assert "tender_queries_kontur_5files_review_pool.json.gz" in prompt
    assert "130 unresolved/review cases" in prompt
    assert "33 probable NOT_FOUND" in prompt
    assert "regression guardrails" in prompt.lower()
    assert "NOT a prescribed answer" in prompt


def test_planner_after_research_hides_public_case_failures() -> None:
    prompt = _planner_after_research_prompt(
        cycle=1,
        max_cycles=5,
        goal="Improve real tender matching.",
        research_context="",
        taxonomy="{}",
        memory=[],
        ledger={},
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


def test_research_first_schemas_keep_pipeline_simple() -> None:
    assert _planner_schema_after_research()["properties"]["action"]["enum"] == ["IMPLEMENT", "DONE"]
    assert _validator_schema()["properties"]["decision"]["enum"] == ["ACCEPT", "REJECT"]
