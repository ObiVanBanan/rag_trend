from __future__ import annotations

from typing import Any

from .documents import tokenize


def effective_remaining_labels(
    remaining_labels: dict[str, Any],
    corrections_payload: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Overlay explicit correction labels on top of the top-3 review labels."""

    merged = {
        str(query_id): dict(label)
        for query_id, label in (remaining_labels or {}).items()
        if isinstance(label, dict)
    }
    for query_id, correction in (corrections_payload.get("corrections", {}) or {}).items():
        if not isinstance(correction, dict):
            continue
        merged[str(query_id)] = {
            key: value
            for key, value in correction.items()
            if key != "candidate_grade_overrides"
        }
    return merged


def retrieval_miss_ids(
    remaining_labels: dict[str, Any],
    corrections_payload: dict[str, Any],
) -> list[str]:
    effective = effective_remaining_labels(remaining_labels, corrections_payload)
    return sorted(
        query_id
        for query_id, label in effective.items()
        if label.get("label_status") == "RETRIEVAL_MISS"
    )


def rejected_ids_for_query(
    query_id: str,
    review_payload: dict[str, Any],
    corrections_payload: dict[str, Any],
) -> set[int]:
    rejected: set[int] = set()
    review_entry = (review_payload.get("queries", {}) or {}).get(query_id, {})
    for ld_id, info in (review_entry.get("candidate_grades", {}) or {}).items():
        if str((info or {}).get("grade") or "").upper() == "REJECT":
            rejected.add(int(ld_id))

    correction = (corrections_payload.get("corrections", {}) or {}).get(query_id, {})
    for ld_id, grade in (correction.get("candidate_grade_overrides", {}) or {}).items():
        if str(grade).upper() == "REJECT":
            rejected.add(int(ld_id))
    return rejected


def lexical_overlap(query: str, product_tokens: list[str]) -> float:
    query_tokens = set(tokenize(query))
    if not query_tokens:
        return 0.0
    product_set = set(product_tokens)
    return len(query_tokens & product_set) / len(query_tokens)


def candidate_evidence_score(
    *,
    strict_status: str | None,
    catalog_bm25_rank: int | None,
    review_rank: int | None,
    overlap: float,
) -> float:
    """Rank deep-review candidates by independent evidence, not by one retriever."""

    score = 0.0
    if strict_status == "PASS":
        score += 100.0
    elif strict_status == "UNKNOWN":
        score += 12.0

    if catalog_bm25_rank is not None:
        score += max(0.0, 80.0 - float(catalog_bm25_rank))
    if review_rank is not None:
        score += max(0.0, 45.0 - float(review_rank))
    score += max(0.0, min(float(overlap), 1.0)) * 25.0
    return score
