from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.bm25_store import BM25Store
from nomenclature_matcher.deep_gold import (
    extract_tender_dn,
    normalize_tender_designation,
    tender_query_product_type,
)
from nomenclature_matcher.documents import build_search_text, load_products_from_csv, tokenize
from nomenclature_matcher.golden_rules import golden_product_snapshot
from nomenclature_matcher.query_constraints import (
    candidate_dn,
    candidate_has_designation,
    candidate_product_type,
)


ROOT = Path(__file__).resolve().parents[1]
_CODE_PATTERNS = (
    re.compile(r"(?<![0-9а-яa-z])\d{1,3}[а-яa-z]{1,5}\d+[а-яa-z0-9-]*(?![0-9а-яa-z])", re.I),
    re.compile(r"(?<![0-9а-яa-z])кш[.\s_-]*[а-яa-z]*[.\s_-]*\d[0-9а-яa-z.\-]*(?![0-9а-яa-z])", re.I),
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare a catalog-wide review artifact for real tender queries that still have no verified positive. "
            "This is intentionally independent of Qdrant/OpenAI: full-catalog BM25 plus deterministic type/DN/designation scans."
        )
    )
    parser.add_argument("--queries", default=str(ROOT / "data" / "tender_queries_v1.json"))
    parser.add_argument(
        "--verified-labels",
        default=str(ROOT / "data" / "tender_queries_v1_verified_labels.json"),
    )
    parser.add_argument("--csv", default=str(ROOT / "ld_products_full_nomenclature.csv"))
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "tender_queries_v1_deep_candidates.json"),
    )
    parser.add_argument("--bm25-limit", type=int, default=250)
    parser.add_argument("--top-k", type=int, default=40)
    return parser


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _extract_designations(query: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for pattern in _CODE_PATTERNS:
        for match in pattern.finditer(query):
            value = normalize_tender_designation(match.group(0))
            if value and value not in seen:
                seen.add(value)
                values.append(value)
    return values


def _overlap(query: str, product_text: str) -> float:
    query_tokens = set(tokenize(query))
    if not query_tokens:
        return 0.0
    product_tokens = set(tokenize(product_text))
    return len(query_tokens & product_tokens) / len(query_tokens)


def _candidate_score(
    *,
    bm25_rank: int | None,
    bm25_score: float | None,
    type_match: bool | None,
    dn_match: bool | None,
    designation_match: bool,
    overlap: float,
) -> float:
    score = 0.0
    if designation_match:
        score += 220.0
    if type_match is True:
        score += 100.0
    elif type_match is False:
        score -= 80.0
    if dn_match is True:
        score += 90.0
    elif dn_match is False:
        score -= 60.0
    if bm25_rank is not None:
        score += max(0.0, 90.0 - 0.3 * bm25_rank)
    if bm25_score is not None:
        score += min(float(bm25_score), 50.0)
    score += max(0.0, min(float(overlap), 1.0)) * 50.0
    return score


def main() -> int:
    args = _parser().parse_args()
    if args.bm25_limit <= 0 or args.top_k <= 0:
        raise SystemExit("--bm25-limit and --top-k must be > 0")

    queries = _read_json(Path(args.queries))
    labels = _read_json(Path(args.verified_labels))
    if not isinstance(queries, list):
        raise SystemExit("queries must be a JSON list")
    if not isinstance(labels, dict):
        raise SystemExit("verified labels must be a JSON object")

    unresolved = [item for item in queries if str(item.get("id")) not in labels]
    products = load_products_from_csv(args.csv)
    products_by_id = {int(product.id): product for product in products}
    search_text_by_id = {int(product.id): build_search_text(product) for product in products}
    bm25 = BM25Store(products)

    output_queries: list[dict[str, Any]] = []
    for item in unresolved:
        query_id = str(item["id"])
        query = str(item["query"])
        expected_type = tender_query_product_type(query)
        expected_dn = extract_tender_dn(query)
        designations = _extract_designations(query)

        bm25_hits = bm25.search(query, args.bm25_limit)
        bm25_by_id = {int(hit.ld_id): (rank, hit) for rank, hit in enumerate(bm25_hits, start=1)}
        pool: set[int] = set(bm25_by_id)

        # Catalog-side deterministic expansion. This is what makes this a real deep review,
        # rather than simply asking the same top-20 retriever for more rows.
        for product in products:
            ld_id = int(product.id)
            product_type = candidate_product_type(product)
            actual_dn = candidate_dn(product)
            if expected_type is not None and product_type == expected_type:
                if expected_dn is None or actual_dn == expected_dn:
                    pool.add(ld_id)
            if designations and any(candidate_has_designation(product, code) for code in designations):
                pool.add(ld_id)

        ranked: list[tuple[float, dict[str, Any]]] = []
        for ld_id in pool:
            product = products_by_id[ld_id]
            product_type = candidate_product_type(product)
            actual_dn = candidate_dn(product)
            type_match = None if expected_type is None else product_type == expected_type
            dn_match = None if expected_dn is None else actual_dn == expected_dn
            designation_match = bool(
                designations and any(candidate_has_designation(product, code) for code in designations)
            )
            bm25_info = bm25_by_id.get(ld_id)
            bm25_rank = bm25_info[0] if bm25_info else None
            bm25_score = float(bm25_info[1].bm25_score) if bm25_info else None
            overlap = _overlap(query, search_text_by_id[ld_id])
            score = _candidate_score(
                bm25_rank=bm25_rank,
                bm25_score=bm25_score,
                type_match=type_match,
                dn_match=dn_match,
                designation_match=designation_match,
                overlap=overlap,
            )
            candidate = golden_product_snapshot(product)
            candidate["deep_evidence"] = {
                "score": score,
                "bm25_rank": bm25_rank,
                "bm25_score": bm25_score,
                "expected_product_type": expected_type,
                "product_type_match": type_match,
                "expected_dn": expected_dn,
                "dn_match": dn_match,
                "query_designations": designations,
                "designation_match": designation_match,
                "lexical_overlap": overlap,
            }
            ranked.append((score, candidate))

        ranked.sort(
            key=lambda row: (
                -row[0],
                row[1].get("ld_id") if row[1].get("ld_id") is not None else 10**12,
            )
        )
        candidates = [candidate for _, candidate in ranked[: args.top_k]]
        output_queries.append(
            {
                "id": query_id,
                "query": query,
                "metadata": {key: value for key, value in item.items() if key not in {"id", "query"}},
                "deep_query_evidence": {
                    "expected_product_type": expected_type,
                    "expected_dn": expected_dn,
                    "designations": designations,
                    "candidate_pool_size": len(pool),
                },
                "candidates": candidates,
            }
        )
        print(
            f"{query_id}: pool={len(pool)} top={len(candidates)} "
            f"type={expected_type} dn={expected_dn} codes={designations}"
        )

    payload = {
        "version": 2,
        "purpose": "Catalog-wide second-pass candidate review for unresolved real tender queries.",
        "retrieval": {
            "bm25_full_catalog": True,
            "bm25_limit": args.bm25_limit,
            "deterministic_type_dn_expansion": True,
            "deterministic_designation_expansion": True,
            "compact_tender_parsing": True,
            "designation_homoglyph_normalization": "latin c -> cyrillic с",
            "primary_product_type_priority": True,
            "qdrant": False,
            "llm": False,
            "top_k": args.top_k,
        },
        "summary": {
            "all_tender_queries": len(queries),
            "already_verified": len(labels),
            "unresolved": len(unresolved),
        },
        "queries": output_queries,
    }
    _write_json(Path(args.output), payload)
    print(json.dumps(payload["summary"], ensure_ascii=False))
    print(f"Saved: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
