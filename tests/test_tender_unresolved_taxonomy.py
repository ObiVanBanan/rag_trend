from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(name: str):
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def test_taxonomy_covers_exactly_unresolved_real_tenders() -> None:
    queries = _read("data/tender_queries_v1.json")
    verified = _read("data/tender_queries_v1_canonical_labels.json")
    taxonomy = _read("data/tender_queries_v1_unresolved_taxonomy.json")

    all_ids = {row["id"] for row in queries}
    unresolved_ids = all_ids - set(verified)
    taxonomy_ids = {row["id"] for row in taxonomy["queries"]}

    assert len(all_ids) == 44
    assert len(verified) == 20
    assert len(unresolved_ids) == 24
    assert taxonomy_ids == unresolved_ids
    assert len(taxonomy["queries"]) == 24


def test_primary_reason_summary_matches_rows() -> None:
    taxonomy = _read("data/tender_queries_v1_unresolved_taxonomy.json")
    rows = taxonomy["queries"]
    counts = Counter(row["primary_reason"] for row in rows)

    assert dict(counts) == taxonomy["summary"]["primary_reason_counts"]
    assert counts == {
        "CATALOG_NO_MATCH": 12,
        "ALIAS_MAPPING_MISS": 7,
        "SCHEMA_GAP": 3,
        "PARSER_MISS": 1,
        "QUERY_UNDERSPECIFIED": 1,
    }
    assert taxonomy["summary"]["primary_reason_counts"]["RETRIEVAL_MISS"] == 0


def test_retrieval_miss_is_not_used_without_known_positive_evidence() -> None:
    taxonomy = _read("data/tender_queries_v1_unresolved_taxonomy.json")

    # A pure retrieval miss requires a known valid catalog target. None of the
    # currently unresolved tender cases has that evidence yet.
    assert all(row["primary_reason"] != "RETRIEVAL_MISS" for row in taxonomy["queries"])


def test_high_value_failure_groups_are_explicit() -> None:
    taxonomy = _read("data/tender_queries_v1_unresolved_taxonomy.json")
    rows = {row["id"]: row for row in taxonomy["queries"]}

    for query_id in {"tender_v1_001", "tender_v1_005", "tender_v1_007"}:
        assert rows[query_id]["primary_reason"] == "ALIAS_MAPPING_MISS"

    for query_id in {"tender_v1_014", "tender_v1_015", "tender_v1_016", "tender_v1_022"}:
        assert rows[query_id]["primary_reason"] == "ALIAS_MAPPING_MISS"
        assert "SCHEMA_GAP" in rows[query_id]["secondary_reasons"]

    for query_id in {
        "tender_v1_024",
        "tender_v1_025",
        "tender_v1_026",
        "tender_v1_027",
        "tender_v1_032",
        "tender_v1_037",
    }:
        assert rows[query_id]["primary_reason"] == "CATALOG_NO_MATCH"

    assert rows["tender_v1_021"]["primary_reason"] == "PARSER_MISS"
    assert rows["tender_v1_040"]["primary_reason"] == "QUERY_UNDERSPECIFIED"
