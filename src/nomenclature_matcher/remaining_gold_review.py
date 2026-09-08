from __future__ import annotations

from typing import Any


TRUSTED_LABEL_SOURCES = {"HUMAN", "HUMAN_VERIFIED_CHATGPT", "SYNTHETIC_NEGATIVE"}


def trusted_reviewed_query_ids(
    base_labels: dict[str, Any],
    chatgpt_verified_labels: dict[str, Any],
) -> set[str]:
    reviewed = set(chatgpt_verified_labels)
    for query_id, label in base_labels.items():
        if not isinstance(label, dict):
            continue
        if label.get("label_status") != "VERIFIED":
            continue
        if label.get("label_source") in TRUSTED_LABEL_SOURCES:
            reviewed.add(str(query_id))
    return reviewed


def candidate_rank(candidate: dict[str, Any]) -> tuple[int, int, int, int, int]:
    hybrid = candidate.get("hybrid_rank") or 999999
    dense = candidate.get("dense_rank") or 999999
    bm25 = candidate.get("bm25_rank") or 999999
    llm_selected = bool(candidate.get("llm_selected"))
    return (
        0 if llm_selected else 1,
        0 if hybrid != 999999 else 1,
        min(hybrid, dense, bm25),
        hybrid,
        min(dense, bm25),
    )


def select_top_candidates(candidates: list[dict[str, Any]], limit: int = 3) -> list[dict[str, Any]]:
    by_id: dict[int, dict[str, Any]] = {}
    for candidate in candidates:
        if not isinstance(candidate, dict) or candidate.get("ld_id") is None:
            continue
        ld_id = int(candidate["ld_id"])
        current = by_id.get(ld_id)
        if current is None or candidate_rank(candidate) < candidate_rank(current):
            by_id[ld_id] = candidate
    return sorted(by_id.values(), key=candidate_rank)[:limit]


def build_remaining_review_payload(
    review_candidates: dict[str, Any],
    base_labels: dict[str, Any],
    chatgpt_verified_labels: dict[str, Any],
    *,
    limit: int = 3,
) -> dict[str, Any]:
    if limit <= 0:
        raise ValueError("limit must be > 0")

    reviewed = trusted_reviewed_query_ids(base_labels, chatgpt_verified_labels)
    remaining: list[dict[str, Any]] = []
    for item in review_candidates.get("queries", []):
        if not isinstance(item, dict) or not item.get("id"):
            continue
        query_id = str(item["id"])
        if query_id in reviewed:
            continue
        remaining.append(
            {
                "id": query_id,
                "query": str(item.get("query") or ""),
                "metadata": item.get("metadata") or {},
                "candidates": select_top_candidates(item.get("candidates", []), limit=limit),
            }
        )

    return {
        "version": 1,
        "purpose": "Human review of previously unreviewed Golden 100 queries using only top candidates.",
        "max_candidates_per_query": limit,
        "reviewed_query_count": len(reviewed),
        "remaining_query_count": len(remaining),
        "queries": remaining,
    }
