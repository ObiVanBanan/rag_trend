from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .eval_v2 import merge_review_candidates
from .review_state import get_accepted_candidate_ids, get_reviewed_candidate_count

EXPANDED_RETRIEVAL_MISS_SOURCE = "expanded_retrieval_miss_review"


def query_has_accept(query_state: dict[str, Any]) -> bool:
    return bool(get_accepted_candidate_ids(query_state))


def query_is_ready_for_expanded_review(query_state: dict[str, Any], total_candidates: int) -> bool:
    return get_reviewed_candidate_count(query_state) >= total_candidates and not query_has_accept(query_state)


def select_queries_for_expanded_review(candidates_payload: dict[str, Any], review_state: dict[str, Any]) -> list[str]:
    selected: list[str] = []
    for query_item in candidates_payload.get("queries", []):
        query_id = query_item["id"]
        query_state = review_state["queries"].get(query_id, {})
        if query_is_ready_for_expanded_review(query_state, len(query_item.get("candidates", []))):
            selected.append(query_id)
    return selected


def build_expanded_review_query(
    query_item: dict[str, Any],
    base_reviewed_ids: set[int],
    dense_candidates,
    bm25_candidates,
    hybrid_candidates,
    max_per_source: int = 100,
) -> dict[str, Any]:
    merged = merge_review_candidates(dense_candidates, bm25_candidates, hybrid_candidates, max_per_source=max_per_source)
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
    hybrid_search_fn,
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
        hybrid_candidates = hybrid_search_fn(query_item["query"], max_per_source)
        report["queries"].append(
            build_expanded_review_query(
                query_item,
                base_ids,
                dense_candidates,
                bm25_candidates,
                hybrid_candidates,
                max_per_source=max_per_source,
            )
        )
    return report
