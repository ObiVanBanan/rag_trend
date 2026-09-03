from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .eval_v2 import select_review_properties
from .review_state import get_accepted_candidate_ids_for_ids, get_reviewed_candidate_count_for_ids

EXPANDED_RETRIEVAL_MISS_SOURCE = "expanded_retrieval_miss_review"


def query_is_ready_for_expanded_review(query_state: dict[str, Any], candidate_ids: list[int | str]) -> bool:
    return get_reviewed_candidate_count_for_ids(query_state, candidate_ids) >= len(candidate_ids) and not get_accepted_candidate_ids_for_ids(query_state, candidate_ids)


def select_queries_for_expanded_review(candidates_payload: dict[str, Any], review_state: dict[str, Any]) -> list[str]:
    selected: list[str] = []
    for query_item in candidates_payload.get("queries", []):
        query_id = query_item["id"]
        query_state = review_state["queries"].get(query_id, {})
        candidate_ids = [candidate["ld_id"] for candidate in query_item.get("candidates", [])]
        if query_is_ready_for_expanded_review(query_state, candidate_ids):
            selected.append(query_id)
    return selected


def _init_expanded_review_candidate(candidate: Any) -> dict[str, Any]:
    technical_properties = select_review_properties(getattr(candidate, "properties", None))
    return {
        "ld_id": candidate.ld_id,
        "article": candidate.article,
        "name": candidate.name,
        "dn": candidate.dn,
        "pn": candidate.pn,
        "joining_type": candidate.joining_type,
        "dense_rank": None,
        "bm25_rank": None,
        "hybrid_rank": None,
        "dense_score": None,
        "bm25_score": None,
        "rrf_score": None,
        "retrieval_sources": list(dict.fromkeys(getattr(candidate, "retrieval_sources", []) or [])),
        "technical_properties": technical_properties,
        "human_grade": None,
        "human_comment": "",
    }


def build_expanded_hybrid_candidates(
    dense_candidates,
    bm25_candidates,
    max_per_source: int = 100,
    rrf_k: int = 60,
) -> list[dict[str, Any]]:
    merged: dict[int, dict[str, Any]] = {}
    for rank, candidate in enumerate(dense_candidates[:max_per_source], 1):
        row = merged.setdefault(candidate.ld_id, _init_expanded_review_candidate(candidate))
        row["article"] = row["article"] or candidate.article
        row["name"] = row["name"] or candidate.name
        row["dn"] = row["dn"] or candidate.dn
        row["pn"] = row["pn"] or candidate.pn
        row["joining_type"] = row["joining_type"] or candidate.joining_type
        row["technical_properties"].update(select_review_properties(getattr(candidate, "properties", None)))
        row["dense_rank"] = rank
        row["dense_score"] = candidate.dense_score
        if "dense" not in row["retrieval_sources"]:
            row["retrieval_sources"].append("dense")

    for rank, candidate in enumerate(bm25_candidates[:max_per_source], 1):
        row = merged.setdefault(candidate.ld_id, _init_expanded_review_candidate(candidate))
        row["article"] = row["article"] or candidate.article
        row["name"] = row["name"] or candidate.name
        row["dn"] = row["dn"] or candidate.dn
        row["pn"] = row["pn"] or candidate.pn
        row["joining_type"] = row["joining_type"] or candidate.joining_type
        row["technical_properties"].update(select_review_properties(getattr(candidate, "properties", None)))
        row["bm25_rank"] = rank
        row["bm25_score"] = candidate.bm25_score
        if "bm25" not in row["retrieval_sources"]:
            row["retrieval_sources"].append("bm25")

    for candidate in merged.values():
        score = 0.0
        if candidate["dense_rank"] is not None:
            score += 1.0 / (rrf_k + candidate["dense_rank"])
        if candidate["bm25_rank"] is not None:
            score += 1.0 / (rrf_k + candidate["bm25_rank"])
        candidate["rrf_score"] = score
        candidate["hybrid_rank"] = None

    ranked = sorted(
        merged.values(),
        key=lambda item: (
            -(item["rrf_score"] or 0.0),
            item["dense_rank"] or 10**9,
            item["bm25_rank"] or 10**9,
            item["ld_id"],
        ),
    )
    for index, candidate in enumerate(ranked, 1):
        candidate["hybrid_rank"] = index
    return ranked[:max_per_source]


def build_expanded_review_query(
    query_item: dict[str, Any],
    base_reviewed_ids: set[int],
    dense_candidates,
    bm25_candidates,
    max_per_source: int = 100,
) -> dict[str, Any]:
    merged = build_expanded_hybrid_candidates(dense_candidates, bm25_candidates, max_per_source=max_per_source)
    filtered_candidates = [candidate for candidate in merged if int(candidate["ld_id"]) not in base_reviewed_ids]
    return {
        "id": query_item["id"],
        "query": query_item["query"],
        "source": EXPANDED_RETRIEVAL_MISS_SOURCE,
        "candidates": filtered_candidates,
    }


def build_expanded_review_report(
    candidates_payload: dict[str, Any],
    review_state: dict[str, Any],
    dense_search_fn,
    bm25_search_fn,
    max_per_source: int = 100,
) -> dict[str, Any]:
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "source": EXPANDED_RETRIEVAL_MISS_SOURCE,
        "queries": [],
    }
    selected_ids = select_queries_for_expanded_review(candidates_payload, review_state)
    for query_item in candidates_payload.get("queries", []):
        if query_item["id"] not in selected_ids:
            continue
        base_ids = {int(candidate["ld_id"]) for candidate in query_item.get("candidates", [])}
        dense_candidates = dense_search_fn(query_item["query"], max_per_source)
        bm25_candidates = bm25_search_fn(query_item["query"], max_per_source)
        report["queries"].append(
            build_expanded_review_query(
                query_item,
                base_ids,
                dense_candidates,
                bm25_candidates,
                max_per_source=max_per_source,
            )
        )
    return report
