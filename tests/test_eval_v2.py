import json
from pathlib import Path

from nomenclature_matcher.documents import LDProduct
from nomenclature_matcher.eval_utils import classify_error_type, recall_at_20, reranker_accuracy
from nomenclature_matcher.eval_v2 import (
    build_v2_label_template,
    merge_review_candidates,
    select_review_properties,
    summarize_v2_diagnostics,
)
from nomenclature_matcher.models import SearchCandidate


def test_v2_queries_have_unique_ids_and_non_empty_text():
    queries = json.loads(Path("data/eval_queries_v2.json").read_text(encoding="utf-8"))
    ids = [item["id"] for item in queries]

    assert len(ids) == len(set(ids))
    assert all(item["query"].strip() for item in queries)


def test_v2_label_template_defaults_to_unreviewed():
    template = build_v2_label_template(["v2_q01", "v2_q02"])

    assert template["v2_q01"]["label_status"] == "UNREVIEWED"
    assert template["v2_q01"]["acceptable_ld_ids"] == []
    assert template["v2_q01"]["expected_status"] is None
    assert template["v2_q01"]["human_comment"] == ""


def test_review_candidate_pool_deduplicates_by_ld_id():
    dense = [
        SearchCandidate(
            ld_id=1,
            name="Dense",
            article="A1",
            score=0.9,
            dense_score=0.9,
            dense_rank=1,
            properties=[{"name": "Тип продукта", "values": ["Кран шаровой"]}],
        )
    ]
    bm25 = [
        SearchCandidate(
            ld_id=1,
            name="Dense",
            article="A1",
            score=1.2,
            bm25_score=1.2,
            bm25_rank=2,
            properties=[{"name": "Материал корпуса", "values": ["Латунь"]}],
        )
    ]
    hybrid = [
        SearchCandidate(
            ld_id=1,
            name="Dense",
            article="A1",
            score=0.03,
            dense_score=0.9,
            dense_rank=1,
            bm25_score=1.2,
            bm25_rank=2,
            rrf_score=0.03,
            retrieval_sources=["dense", "bm25"],
            properties=[{"name": "Присоединение", "values": ["Фланцевое"]}],
        )
    ]

    rows = merge_review_candidates(dense, bm25, hybrid)

    assert len(rows) == 1
    row = rows[0]
    assert row["ld_id"] == 1
    assert row["dense_rank"] == 1
    assert row["bm25_rank"] == 1
    assert row["hybrid_rank"] == 1
    assert row["technical_properties"]["Тип продукта"] == ["Кран шаровой"]
    assert row["technical_properties"]["Материал корпуса"] == ["Латунь"]
    assert row["technical_properties"]["Присоединение"] == ["Фланцевое"]


def test_temperature_property_is_kept_in_review_properties():
    properties = [
        {"name": "Температура рабочей среды, °С", "values": ["-40…200"]},
        {"name": "Вес, кг", "values": ["12"]},
    ]

    assert select_review_properties(properties) == {"Температура рабочей среды, °С": ["-40…200"]}


def test_old_q04_is_unreviewed_in_trusted_baseline():
    labels = json.loads(Path("data/eval_labels.json").read_text(encoding="utf-8"))
    q04 = labels["q04"]

    assert q04["label_status"] == "UNREVIEWED"
    assert q04["acceptable_ld_ids"] == []
    assert q04["expected_status"] is None


def test_unreviewed_queries_do_not_affect_metrics():
    results = [
        {
            "label_status": "VERIFIED",
            "expected_status": "MATCHED",
            "acceptable_ld_ids": [1],
            "dense_top20": [{"ld_id": 1}],
            "bm25_top20": [{"ld_id": 1}],
            "hybrid_top20": [{"ld_id": 1}],
            "deepseek_selected_ld_ids": [1],
            "deepseek_status": "MATCHED",
            "dense_hit": True,
            "bm25_hit": True,
            "hybrid_hit": True,
        },
        {
            "label_status": "UNREVIEWED",
            "expected_status": None,
            "acceptable_ld_ids": [],
            "dense_top20": [{"ld_id": 2}],
            "bm25_top20": [{"ld_id": 2}],
            "hybrid_top20": [{"ld_id": 2}],
            "deepseek_selected_ld_ids": [2],
            "deepseek_status": "MATCHED",
            "dense_hit": None,
            "bm25_hit": None,
            "hybrid_hit": None,
        },
    ]

    assert recall_at_20(results, "dense_top20") == 1.0
    assert reranker_accuracy(results) == 1.0


def test_hybrid_recovery_is_counted_as_ok():
    error_type = classify_error_type(
        label_status="VERIFIED",
        expected_status="MATCHED",
        dense_hit=False,
        bm25_hit=True,
        hybrid_hit=True,
        reranker_success=True,
        deepseek_status="MATCHED",
    )
    summary = summarize_v2_diagnostics(
        [
            {
                "label_status": "VERIFIED",
                "expected_status": "MATCHED",
                "dense_hit": False,
                "bm25_hit": True,
                "hybrid_hit": True,
                "reranker_success": True,
            }
        ]
    )

    assert error_type == "OK"
    assert summary["hybrid_recovered_dense_miss"] == 1
