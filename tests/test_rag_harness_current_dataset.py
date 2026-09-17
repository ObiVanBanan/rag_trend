from __future__ import annotations

from harness_rag.current_dataset_runner import compare_compact_rows, summarize_rows


def _row(
    *,
    query: str,
    status: str,
    reason: str,
    constraints: dict | None = None,
    candidates: list[dict] | None = None,
    product: dict | None = None,
) -> dict:
    interpretation = {
        "constraints": constraints or {},
        "hard_constraints": constraints or {},
    }
    return {
        "query": query,
        "status": status,
        "ld_product": product,
        "debug": {
            "reason": reason,
            "query_interpretation": interpretation,
            "candidates": candidates or [],
            "selected": [],
        },
    }


def test_summarize_rows_exposes_hard_constraint_failure() -> None:
    rows = [
        _row(
            query="Кран шаровой Ду100",
            status="NOT_FOUND",
            reason="HARD_CONSTRAINT_FILTER: no candidates survived",
            constraints={
                "product_type": "ball_valve",
                "dn": 100,
                "pn_min_mpa": None,
                "joining_type": None,
                "thread_type": None,
                "working_medium": None,
                "valve_type": None,
                "valve_designation": None,
                "body_material": None,
                "body_material_grade": None,
                "bore_type": None,
                "control": None,
                "catalog_scope": "in_scope",
                "ambiguous": False,
                "comment": "",
            },
            candidates=[
                {
                    "ld_id": 1,
                    "name": "Кран шаровой Ду50",
                    "article": "A-1",
                    "dn": 50,
                    "properties": [],
                }
            ],
        )
    ]

    summary, compact = summarize_rows(rows)

    assert summary["total"] == 1
    assert summary["stage_counts"] == {"HARD_CONSTRAINT_FILTER": 1}
    assert summary["hard_filter_failed_constraints"] == {"dn": 1}
    assert compact[0]["hard_filter_failed_constraints"] == {"dn": 1}


def test_compare_compact_rows_records_stage_and_product_transitions() -> None:
    champion = [
        {
            "query": "VALTEC VT.214",
            "status": "NOT_FOUND",
            "stage": "RERANK_NOT_FOUND",
            "product": None,
            "hard_constraints": {"product_type": "ball_valve"},
            "hard_filter_failed_constraints": {},
        },
        {
            "query": "Кран Ду50",
            "status": "MATCHED",
            "stage": "MATCHED",
            "product": {"article": "OLD"},
            "hard_constraints": {"product_type": "ball_valve", "dn": 50},
            "hard_filter_failed_constraints": {},
        },
    ]
    candidate = [
        {
            "query": "VALTEC VT.214",
            "status": "MATCHED",
            "stage": "MATCHED",
            "product": {"article": "NEW"},
            "hard_constraints": {"product_type": "ball_valve"},
            "hard_filter_failed_constraints": {},
        },
        {
            "query": "Кран Ду50",
            "status": "NOT_FOUND",
            "stage": "HARD_CONSTRAINT_FILTER",
            "product": None,
            "hard_constraints": {"product_type": "ball_valve", "dn": 50},
            "hard_filter_failed_constraints": {"dn": 3},
        },
    ]

    delta = compare_compact_rows(champion, candidate)

    assert delta["changed_rows"] == 2
    assert delta["transition_counts"]["RERANK_NOT_FOUND->MATCHED"] == 1
    assert delta["transition_counts"]["MATCHED->HARD_CONSTRAINT_FILTER"] == 1
    # Regressions are intentionally shown first to the validator.
    assert delta["changed_examples"][0]["query"] == "Кран Ду50"


def test_summarize_rows_does_not_treat_matched_count_as_accuracy() -> None:
    rows = [
        _row(
            query="A",
            status="MATCHED",
            reason="matched",
            constraints={"product_type": "ball_valve"},
            product={"article": "1", "name": "A"},
        ),
        _row(
            query="B",
            status="NOT_FOUND",
            reason="QUERY_REJECTED: ambiguous",
            constraints={"product_type": "other", "ambiguous": True},
        ),
    ]

    summary, _ = summarize_rows(rows)

    assert summary["status_counts"] == {"MATCHED": 1, "NOT_FOUND": 1}
    assert summary["matched_rate"] == 0.5
    assert "accuracy" not in summary
