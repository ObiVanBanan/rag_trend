from __future__ import annotations

import gzip
import json
import re
from pathlib import Path
from typing import Any, Iterable

from .runtime import ROOT


QUERY_DATA = "data/tender_queries_kontur_5files.json.gz"
GOLD_DATA = "data/tender_queries_kontur_5files_labels_gold_v1.json.gz"
REVIEW_DATA = "data/tender_queries_kontur_5files_review_pool.json.gz"
PROVISIONAL_DATA = "data/tender_queries_kontur_5files_provisional_not_found.json.gz"
DIGEST_VERSION = 1


_CLASS_PATTERNS: dict[str, re.Pattern[str]] = {
    "ball_valve": re.compile(r"(?:кран[^\n]{0,30}шар|шаров(?:ой|ый|ого|ые|ая))", re.IGNORECASE),
    "butterfly_valve": re.compile(r"\bзатвор", re.IGNORECASE),
    "filter_or_water_treatment": re.compile(
        r"(?:фильтр|грязевик|картридж|эфг|сорбент|водоочист|обезжелез|водоподготов)",
        re.IGNORECASE,
    ),
    "service_action": re.compile(
        r"(?:монтаж|демонтаж|ремонт|обслужив|тех(?:ническ\w*)?\s*обслуж|установк|разборк|сняти)",
        re.IGNORECASE,
    ),
    "thread_or_inch": re.compile(
        r"(?:резьб|муфт|\b(?:вр|нр|вн)\b|\b\d+(?:[.,]\d+)?\s*(?:дюйм|[\"″])|[¼½¾⅜⅝⅞])",
        re.IGNORECASE,
    ),
    "latin_or_model_token": re.compile(r"(?:\b[A-Za-z]{2,}\b|\b(?=[A-Za-zА-Яа-яЁё0-9._/-]*\d)(?=[A-Za-zА-Яа-яЁё0-9._/-]*[A-Za-z])[A-Za-zА-Яа-яЁё0-9._/-]{4,}\b)"),
}


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("rows", "queries", "items", "results", "labels", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _load_gzip_json(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return _rows(json.load(handle)), None
    except (OSError, EOFError, json.JSONDecodeError) as exc:
        return [], f"{type(exc).__name__}: {exc}"


def _row_id(row: dict[str, Any]) -> str:
    for key in ("id", "query_id", "row_id"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _query_text(row: dict[str, Any]) -> str:
    for key in ("query", "text", "nomenclature", "name", "input"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
    return ""


def reconstruct_review_rows(
    all_rows: Iterable[dict[str, Any]],
    gold_rows: Iterable[dict[str, Any]],
    provisional_rows: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    excluded = {_row_id(row) for row in [*gold_rows, *provisional_rows] if _row_id(row)}
    result = [row for row in all_rows if _row_id(row) and _row_id(row) not in excluded]
    return sorted(result, key=lambda row: _row_id(row))


def _class_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {name: 0 for name in _CLASS_PATTERNS}
    for row in rows:
        text = _query_text(row)
        if not text:
            continue
        for name, pattern in _CLASS_PATTERNS.items():
            if pattern.search(text):
                counts[name] += 1
    return counts


def _representative_samples(rows: list[dict[str, Any]], per_class: int = 3) -> dict[str, list[dict[str, str]]]:
    samples: dict[str, list[dict[str, str]]] = {name: [] for name in _CLASS_PATTERNS}
    for row in sorted(rows, key=lambda item: _row_id(item)):
        text = _query_text(row)
        if not text:
            continue
        for name, pattern in _CLASS_PATTERNS.items():
            if len(samples[name]) >= per_class or not pattern.search(text):
                continue
            samples[name].append({"id": _row_id(row), "query": text[:240]})
    return samples


def _canonical_alternate_count(rows: list[dict[str, Any]]) -> int | None:
    try:
        from nomenclature_matcher.query_canonicalization import canonicalize_retrieval_query
    except Exception:
        return None

    count = 0
    for row in rows:
        text = _query_text(row)
        if not text:
            continue
        try:
            result = canonicalize_retrieval_query(text)
        except Exception:
            continue
        if getattr(result, "canonical_query", None):
            count += 1
    return count


def build_research_corpus_digest(
    root: Path = ROOT,
    *,
    query_data: str = QUERY_DATA,
    gold_data: str = GOLD_DATA,
    review_data: str = REVIEW_DATA,
    provisional_data: str = PROVISIONAL_DATA,
) -> dict[str, Any]:
    all_rows, all_error = _load_gzip_json(root / query_data)
    gold_rows, gold_error = _load_gzip_json(root / gold_data)
    review_rows, review_error = _load_gzip_json(root / review_data)
    provisional_rows, provisional_error = _load_gzip_json(root / provisional_data)

    review_source = "artifact"
    if review_error or not review_rows:
        review_rows = reconstruct_review_rows(all_rows, gold_rows, provisional_rows)
        review_source = "reconstructed_from_all_minus_gold_minus_provisional"

    return {
        "version": DIGEST_VERSION,
        "counts": {
            "all_queries": len(all_rows),
            "gold_labels": len(gold_rows),
            "review_pool": len(review_rows),
            "provisional_not_found": len(provisional_rows),
        },
        "review_pool_source": review_source,
        "input_health": {
            "all_queries": {"ok": all_error is None, "error": all_error},
            "gold_labels": {"ok": gold_error is None, "error": gold_error},
            "review_pool": {"ok": review_error is None, "error": review_error},
            "provisional_not_found": {"ok": provisional_error is None, "error": provisional_error},
        },
        "review_class_counts": _class_counts(review_rows),
        "review_canonical_alternate_count": _canonical_alternate_count(review_rows),
        "representative_review_samples": _representative_samples(review_rows),
        "semantics": {
            "gold_labels": "first-pass high-confidence reference labels; not exhaustive final truth",
            "provisional_not_found": "probable NOT_FOUND; not hard GOLD",
            "review_pool": "unresolved real-tender discovery evidence; not an optimization target",
        },
    }
