from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_verified_tender_harness_cases_match_human_labels() -> None:
    cases_payload = json.loads(
        (ROOT / "data" / "tender_queries_v1_harness_cases.json").read_text(encoding="utf-8")
    )
    labels = json.loads(
        (ROOT / "data" / "tender_queries_v1_canonical_labels.json").read_text(encoding="utf-8")
    )
    cases = cases_payload["cases"]

    assert {case["id"] for case in cases} == set(labels)
    assert len(cases) == 20
    assert sum(case["split"] == "CORE" for case in cases) == 11
    assert sum(case["split"] == "EXTENDED" for case in cases) == 9
    assert sum(bool(case["hard_gate"]) for case in cases) == 11

    for case in cases:
        label = labels[case["id"]]
        assert label["label_status"] == "VERIFIED"
        assert label["label_source"] == "HUMAN_VERIFIED_CHATGPT"
        assert label["expected_status"] == "MATCHED"
        assert set(case["known_positive_ids"]) == set(label["acceptable_ld_ids"])
        assert case["known_positive_ids_exhaustive"] is False


def test_extended_tender_cases_are_not_silently_promoted() -> None:
    payload = json.loads(
        (ROOT / "data" / "tender_queries_v1_harness_cases.json").read_text(encoding="utf-8")
    )
    cases = {case["id"]: case for case in payload["cases"]}

    for query_id in {
        "tender_v1_002",
        "tender_v1_011",
        "tender_v1_012",
        "tender_v1_013",
        "tender_v1_020",
    }:
        case = cases[query_id]
        assert case["split"] == "EXTENDED"
        assert case["hard_gate"] is False
        assert case["extended_reasons"]


def test_generic_real_tender_cases_use_constraint_gate_not_exact_id_membership() -> None:
    payload = json.loads(
        (ROOT / "data" / "tender_queries_v1_harness_cases.json").read_text(encoding="utf-8")
    )
    cases = {case["id"]: case for case in payload["cases"]}

    assert cases["tender_v1_028"]["requirements"] == {"product_type": "filter", "dn": 50}
    assert cases["tender_v1_030"]["requirements"] == {"product_type": "ball_valve", "dn": 150}
    assert cases["tender_v1_034"]["requirements"] == {
        "product_type": "ball_valve",
        "dn": 50,
        "joining_type": "flanged",
    }
