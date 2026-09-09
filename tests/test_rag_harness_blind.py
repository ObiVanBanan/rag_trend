from __future__ import annotations

import pytest

from harness_rag.blind import BlindHoldoutError, build_blind_payload


def _source() -> dict:
    return {
        "cases": [
            {
                "id": "p1",
                "query": "Кран шаровый Ду50",
                "split": "CORE",
                "hard_gate": True,
                "expected_status": "MATCHED",
                "requirements": {"product_type": "ball_valve", "dn": 50},
                "known_positive_ids": [1],
                "known_rejected_ids": [],
                "known_unsure_ids": [],
                "known_positive_ids_exhaustive": False,
                "metadata": {"source_file": "public.xlsx"},
            },
            {
                "id": "n1",
                "query": "Насос 5 кВт",
                "split": "NEGATIVE",
                "hard_gate": True,
                "expected_status": "NOT_FOUND",
                "requirements": {},
                "known_positive_ids": [],
                "known_rejected_ids": [],
                "known_unsure_ids": [],
                "known_positive_ids_exhaustive": False,
            },
            {
                "id": "diagnostic",
                "query": "не входит",
                "split": "EXTENDED",
                "hard_gate": False,
            },
        ]
    }


def test_builds_only_hard_cases_and_hides_public_ids() -> None:
    payload = build_blind_payload(
        source=_source(),
        query_map=[
            {"source_id": "p1", "query": "Шаровой кран DN50"},
            {"source_id": "n1", "query": "Электродвигатель 5 кВт"},
        ],
    )

    assert payload["summary"] == {"total": 2, "CORE": 1, "NEGATIVE": 1, "hard_gate": 2}
    assert [case["id"] for case in payload["cases"]] == ["blind_001", "blind_002"]
    assert payload["cases"][0]["known_positive_ids"] == [1]
    assert payload["cases"][0]["metadata"] == {"origin": "blind_holdout"}
    assert "source_file" not in str(payload)
    assert "p1" not in {case["id"] for case in payload["cases"]}


def test_requires_exact_coverage_of_public_hard_gate_ids() -> None:
    with pytest.raises(BlindHoldoutError, match="cover every hard-gate"):
        build_blind_payload(
            source=_source(),
            query_map=[{"source_id": "p1", "query": "Шаровой кран DN50"}],
        )


def test_rejects_identical_public_query() -> None:
    with pytest.raises(BlindHoldoutError, match="must differ"):
        build_blind_payload(
            source=_source(),
            query_map=[
                {"source_id": "p1", "query": "Кран шаровый Ду50"},
                {"source_id": "n1", "query": "Электродвигатель 5 кВт"},
            ],
        )
