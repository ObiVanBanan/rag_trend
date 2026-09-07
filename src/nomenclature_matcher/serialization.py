from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from .models import MatchResult, SearchCandidate, SelectedMatch


def _clean(value: Any) -> Any:
    if is_dataclass(value):
        return _clean(asdict(value))
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    return value


def production_product(candidate: SearchCandidate | None) -> dict[str, Any] | None:
    if candidate is None:
        return None
    return {"name": candidate.name, "article": candidate.article}


def candidate_debug_payload(candidate: SearchCandidate, candidate_id: int | None = None) -> dict[str, Any]:
    payload = {
        "candidate_id": candidate_id,
        "ld_id": candidate.ld_id,
        "article": candidate.article,
        "name": candidate.name,
        "dn": candidate.dn,
        "pn": candidate.pn,
        "joining_type": candidate.joining_type,
        "price": candidate.price,
        "url": candidate.url,
        "properties": _clean(candidate.properties),
        "search_text": candidate.search_text,
        "score": candidate.score,
        "dense_score": candidate.dense_score,
        "dense_rank": candidate.dense_rank,
        "bm25_score": candidate.bm25_score,
        "bm25_rank": candidate.bm25_rank,
        "rrf_score": candidate.rrf_score,
        "retrieval_sources": list(candidate.retrieval_sources),
    }
    return {key: value for key, value in payload.items() if value is not None}


def selected_debug_payload(selected: SelectedMatch) -> dict[str, Any]:
    return {
        "candidate_id": selected.candidate_id,
        "ld_id": selected.ld_id,
        "article": selected.article,
        "name": selected.name,
        "dn": selected.dn,
        "pn": selected.pn,
        "joining_type": selected.joining_type,
        "url": selected.url,
        "dense_score": selected.dense_score,
        "bm25_score": selected.bm25_score,
        "rrf_score": selected.rrf_score,
        "llm_confidence": selected.llm_confidence,
        "reason": selected.reason,
    }


def match_result_payload(result: MatchResult, *, include_debug: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "query": result.query,
        "status": result.status,
        "score": result.score,
        "ld_product": production_product(result.ld_product),
    }
    if include_debug:
        payload["debug"] = {
            "reason": result.reason,
            "candidates": [
                candidate_debug_payload(candidate, index)
                for index, candidate in enumerate(result.candidates, 1)
            ],
            "selected": [selected_debug_payload(item) for item in result.selected],
        }
    return _clean(payload)


def match_results_payload(results: list[MatchResult], *, include_debug: bool = True) -> list[dict[str, Any]]:
    return [match_result_payload(result, include_debug=include_debug) for result in results]
