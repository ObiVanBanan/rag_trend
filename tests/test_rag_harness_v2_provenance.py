from __future__ import annotations

import gzip
import json
from pathlib import Path

from harness_rag.corpus_digest import build_research_corpus_digest, reconstruct_review_rows
from harness_rag.provenance_runner import (
    CampaignScopedStateDir,
    _safe_campaign_id,
    _split_mechanism_ledger,
)


def _write_gzip_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


def test_campaign_scoped_state_dir_routes_only_campaign_artifacts(tmp_path: Path) -> None:
    scoped = CampaignScopedStateDir(tmp_path, "campaign-123")

    assert scoped / "runs" == tmp_path / "campaigns" / "campaign-123" / "runs"
    assert scoped / "FINAL_REPORT_V2.md" == tmp_path / "campaigns" / "campaign-123" / "FINAL_REPORT_V2.md"
    assert scoped / "final" == tmp_path / "campaigns" / "campaign-123" / "final"
    assert scoped / "state.json" == tmp_path / "state.json"
    assert scoped / "history_v2.jsonl" == tmp_path / "history_v2.jsonl"


def test_campaign_id_is_path_safe() -> None:
    assert _safe_campaign_id({"campaign_id": "2026/09/11 12:00:00Z"}) == "2026-09-11-12-00-00Z"
    assert _safe_campaign_id({}) == "legacy-unscoped"


def test_mechanism_entries_are_moved_out_of_hypothesis_ledger() -> None:
    state = {
        "hypothesis_ledger": {
            "aliases": {"attempts": 2},
            "mechanism::candidate_recall": {"attempts": 3, "kind": "causal_mechanism"},
        }
    }

    _split_mechanism_ledger(state)

    assert state["hypothesis_ledger"] == {"aliases": {"attempts": 2}}
    assert state["mechanism_ledger"]["candidate_recall"]["attempts"] == 3


def test_review_rows_reconstruct_as_all_minus_gold_minus_provisional() -> None:
    all_rows = [
        {"id": "q1", "query": "Кран шаровой"},
        {"id": "q2", "query": "Фильтр"},
        {"id": "q3", "query": "Монтаж крана"},
        {"id": "q4", "query": "Затвор"},
    ]
    gold = [{"id": "q1"}]
    provisional = [{"id": "q4"}]

    review = reconstruct_review_rows(all_rows, gold, provisional)

    assert [row["id"] for row in review] == ["q2", "q3"]


def test_digest_falls_back_when_review_gzip_is_corrupt(tmp_path: Path) -> None:
    data = tmp_path / "data"
    all_path = data / "all.json.gz"
    gold_path = data / "gold.json.gz"
    review_path = data / "review.json.gz"
    provisional_path = data / "provisional.json.gz"

    _write_gzip_json(
        all_path,
        [
            {"id": "q1", "query": "Кран шаровой резьбовой 1/2\""},
            {"id": "q2", "query": "Фильтр картриджный"},
            {"id": "q3", "query": "Монтаж крана шарового"},
            {"id": "q4", "query": "Затвор дисковый"},
        ],
    )
    _write_gzip_json(gold_path, [{"id": "q1", "query": "Кран шаровой резьбовой 1/2\""}])
    review_path.write_bytes(b"not-a-valid-gzip")
    _write_gzip_json(provisional_path, [{"id": "q4", "query": "Затвор дисковый"}])

    digest = build_research_corpus_digest(
        tmp_path,
        query_data="data/all.json.gz",
        gold_data="data/gold.json.gz",
        review_data="data/review.json.gz",
        provisional_data="data/provisional.json.gz",
    )

    assert digest["counts"] == {
        "all_queries": 4,
        "gold_labels": 1,
        "review_pool": 2,
        "provisional_not_found": 1,
    }
    assert digest["review_pool_source"] == "reconstructed_from_all_minus_gold_minus_provisional"
    assert digest["input_health"]["review_pool"]["ok"] is False
    assert digest["review_class_counts"]["filter_or_water_treatment"] == 1
    assert digest["review_class_counts"]["service_action"] == 1
