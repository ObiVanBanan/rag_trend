from __future__ import annotations

from collections.abc import Iterable


REVIEW_PROPERTY_NAMES = {
    "Тип продукта",
    "Материал корпуса",
    "Присоединение",
    "Номинальный диаметр, DN",
    "Номинальное давление, МПа",
    "Рабочая среда",
    "Управление",
    "Тип резьбы",
    "Тип прохода",
    "Серия",
    "Температура рабочей среды, °С",
    "Температура окружающей среды, °С",
}


def build_v2_label_template(query_ids: Iterable[str]) -> dict[str, dict]:
    return {
        query_id: {
            "label_status": "UNREVIEWED",
            "acceptable_ld_ids": [],
            "expected_status": None,
            "human_comment": "",
        }
        for query_id in query_ids
    }


def _format_values(values) -> list[str]:
    if isinstance(values, list):
        return [str(value) for value in values if str(value).strip()]
    if values in (None, ""):
        return []
    return [str(values)]


def select_review_properties(properties) -> dict[str, list[str]]:
    selected: dict[str, list[str]] = {}
    for prop in properties or []:
        name = prop.get("name")
        if name not in REVIEW_PROPERTY_NAMES:
            continue
        values = _format_values(prop.get("values"))
        if values:
            selected[name] = values
    return selected


def _init_review_candidate(candidate) -> dict:
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
        "retrieval_sources": [],
        "technical_properties": select_review_properties(getattr(candidate, "properties", None)),
        "human_grade": None,
        "human_comment": "",
    }


def merge_review_candidates(dense_candidates, bm25_candidates, hybrid_candidates) -> list[dict]:
    merged: dict[int, dict] = {}
    for source_name, candidates, rank_field, score_field in (
        ("dense", dense_candidates, "dense_rank", "dense_score"),
        ("bm25", bm25_candidates, "bm25_rank", "bm25_score"),
        ("hybrid", hybrid_candidates, "hybrid_rank", "rrf_score"),
    ):
        for rank, candidate in enumerate(candidates[:10], 1):
            row = merged.setdefault(candidate.ld_id, _init_review_candidate(candidate))
            row["article"] = row["article"] or candidate.article
            row["name"] = row["name"] or candidate.name
            row["dn"] = row["dn"] or candidate.dn
            row["pn"] = row["pn"] or candidate.pn
            row["joining_type"] = row["joining_type"] or candidate.joining_type
            row["technical_properties"].update(select_review_properties(getattr(candidate, "properties", None)))
            row[rank_field] = rank
            if score_field == "dense_score":
                row["dense_score"] = candidate.dense_score
            elif score_field == "bm25_score":
                row["bm25_score"] = candidate.bm25_score
            else:
                row["hybrid_rank"] = rank
                row["rrf_score"] = candidate.rrf_score
            if source_name not in row["retrieval_sources"]:
                row["retrieval_sources"].append(source_name)

    def sort_key(item: dict) -> tuple[int, int, int, int, int, int]:
        hybrid_rank = item["hybrid_rank"] or 999
        dense_rank = item["dense_rank"] or 999
        bm25_rank = item["bm25_rank"] or 999
        best_rank = min(hybrid_rank, dense_rank, bm25_rank)
        return (
            0 if item["hybrid_rank"] is not None else 1,
            best_rank,
            hybrid_rank,
            dense_rank,
            bm25_rank,
            item["ld_id"],
        )

    return sorted(merged.values(), key=sort_key)


def summarize_v2_diagnostics(results: list[dict]) -> dict[str, int]:
    summary = {
        "dense_only_hits": 0,
        "bm25_only_hits": 0,
        "both_hit": 0,
        "neither_hit": 0,
        "hybrid_recovered_dense_miss": 0,
    }
    for item in results:
        if item.get("label_status") != "VERIFIED" or item.get("expected_status") != "MATCHED":
            continue
        dense_hit = item.get("dense_hit") is True
        bm25_hit = item.get("bm25_hit") is True
        hybrid_hit = item.get("hybrid_hit") is True
        if dense_hit and bm25_hit:
            summary["both_hit"] += 1
        elif dense_hit:
            summary["dense_only_hits"] += 1
        elif bm25_hit:
            summary["bm25_only_hits"] += 1
        else:
            summary["neither_hit"] += 1
        if not dense_hit and bm25_hit and hybrid_hit:
            summary["hybrid_recovered_dense_miss"] += 1
    return summary
