from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> dict:
    return json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))


def test_canonical_tender_labels_are_union_of_both_human_passes() -> None:
    first = _load("tender_queries_v1_verified_labels.json")
    deep = _load("tender_queries_v1_deep_verified_labels.json")
    canonical = _load("tender_queries_v1_canonical_labels.json")

    assert set(first).isdisjoint(deep)
    assert set(canonical) == set(first) | set(deep)
    assert len(first) == 11
    assert len(deep) == 9
    assert len(canonical) == 20

    for query_id, label in canonical.items():
        source = first.get(query_id) or deep.get(query_id)
        assert source is not None
        assert label["label_status"] == "VERIFIED"
        assert label["expected_status"] == "MATCHED"
        assert label["label_source"] == "HUMAN_VERIFIED_CHATGPT"
        assert set(label["acceptable_ld_ids"]) == set(source["acceptable_ld_ids"])
        assert label["acceptable_ld_ids"]
