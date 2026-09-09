from __future__ import annotations

import re
from typing import Any

from .documents import tokenize
from .query_constraints import canonical_product_type


_TENDER_DN_PATTERNS = (
    # Real tender exports often remove spaces completely: `Ду32Ру16`,
    # `шаровыйфланцевыйДу50`, `шаровыйДу25-2шт`.
    re.compile(r"(?:dn|ду)\s*=?\s*(\d{1,4})", re.I),
    re.compile(r"номинальн\w*\s+диаметр\w*(?:\s+dn)?\s*(\d{1,4})", re.I),
)


def extract_tender_dn(query: str) -> int | None:
    """Extract an explicitly requested DN even from compact tender text.

    The helper intentionally only treats DN/Ду markers (or an explicit
    `номинальный диаметр`) as nominal diameter evidence. A bare `d 108 мм` may be
    an outside pipe diameter and is therefore not silently converted to DN.
    """

    for pattern in _TENDER_DN_PATTERNS:
        match = pattern.search(query)
        if not match:
            continue
        value = int(match.group(1))
        if value > 0:
            return value
    return None


def tender_query_product_type(query: str) -> str | None:
    """Infer the primary requested product without letting kit text hijack it.

    `canonical_product_type()` quite reasonably classifies a standalone
    "комплект ответных фланцев" as an accessory. Real tenders, however, often say
    "кран ... с комплектом ответных фланцев". In that form the primary product is
    still the ball valve, so primary-product cues are checked first here.
    """

    value = str(query or "").lower().replace("ё", "е")
    if "кран" in value and "шар" in value:
        return "ball_valve"
    if "затвор" in value and ("диск" in value or "поворот" in value):
        return "butterfly_valve"
    if "задвиж" in value:
        return "gate_valve"
    if "клапан" in value and "обрат" in value:
        return "check_valve"
    if "фильтр" in value:
        return "filter"
    if "фланец" in value or "фланцы" in value:
        return "flange"

    result = canonical_product_type(value)
    return None if result == "other" else result


def normalize_tender_designation(value: str) -> str:
    """Normalize a tender designation for exact catalog matching.

    Procurement text frequently mixes the Latin `c` and Cyrillic `с` in Russian
    valve codes (for example `11c67п` vs catalog `11с67п`). Only this observed,
    high-confidence homoglyph is normalized; broad visual substitutions would be
    too risky for GOLD construction.
    """

    compact = re.sub(r"\s+", "", str(value or "").lower().replace("ё", "е"))
    if any(char.isdigit() for char in compact):
        compact = compact.replace("c", "с")
    return compact.strip(".,;:")


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
