from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from nomenclature_matcher.bm25_store import BM25Store
from nomenclature_matcher.documents import load_products_from_csv
from nomenclature_matcher.embeddings import OpenAIEmbedder
from nomenclature_matcher.hybrid_retriever import HybridRetriever
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.qdrant_store import QdrantStore
from nomenclature_matcher.reranker import DeepSeekReranker
from nomenclature_matcher.settings import Settings


_LD_ID_RE = re.compile(r"/products/(\d+)--")


def _clean(value: str | None) -> str:
    return " ".join(str(value or "").split())


def _norm(value: str | None) -> str:
    return _clean(value).lower().replace("ё", "е")


def _ld_id_from_url(url: str) -> int:
    match = _LD_ID_RE.search(url or "")
    if not match:
        raise ValueError(f"Cannot extract LD id from URL: {url!r}")
    return int(match.group(1))


def load_cases(mapping_path: Path) -> list[dict]:
    grouped: dict[str, dict] = {}
    with mapping_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {
            "steel_name",
            "steel_article",
            "steel_url",
            "ld_name",
            "ld_article",
            "ld_url",
            "match_score",
            "match_max",
        }
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Mapping CSV misses columns: {sorted(missing)}")

        for row_no, row in enumerate(reader, start=2):
            score = int(row["match_score"])
            max_score = int(row["match_max"])
            if score < max_score:
                raise ValueError(
                    f"Row {row_no} is not a fully accepted mapping: "
                    f"match_score={score} < match_max={max_score}"
                )

            query = _clean(row["steel_name"])
            key = _norm(query)
            if not key:
                raise ValueError(f"Row {row_no} has empty steel_name")

            case = grouped.setdefault(
                key,
                {
                    "query": query,
                    "competitor_articles": set(),
                    "competitor_urls": set(),
                    "acceptable_ld_ids": set(),
                    "acceptable_ld_articles": set(),
                    "acceptable_ld_names": set(),
                    "source_rows": 0,
                },
            )
            if case["query"] != query:
                raise ValueError(f"Normalized competitor-name collision for {query!r}")

            case["source_rows"] += 1
            case["acceptable_ld_ids"].add(_ld_id_from_url(row["ld_url"]))
            if _clean(row["steel_article"]):
                case["competitor_articles"].add(_clean(row["steel_article"]))
            if _clean(row["steel_url"]):
                case["competitor_urls"].add(_clean(row["steel_url"]))
            if _clean(row["ld_article"]):
                case["acceptable_ld_articles"].add(_clean(row["ld_article"]))
            if _clean(row["ld_name"]):
                case["acceptable_ld_names"].add(_clean(row["ld_name"]))

    cases = []
    for key in sorted(grouped):
        item = grouped[key]
        cases.append(
            {
                "query": item["query"],
                "competitor_articles": sorted(item["competitor_articles"]),
                "competitor_urls": sorted(item["competitor_urls"]),
                "acceptable_ld_ids": sorted(item["acceptable_ld_ids"]),
                "acceptable_ld_articles": sorted(item["acceptable_ld_articles"]),
                "acceptable_ld_names": sorted(item["acceptable_ld_names"]),
                "acceptable_count": len(item["acceptable_ld_ids"]),
                "source_rows": item["source_rows"],
            }
        )
    return cases


def _build_matcher(products, settings: Settings, bm25_store: BM25Store | None = None):
    embedder = OpenAIEmbedder(settings)
    qdrant = QdrantStore(settings)
    hybrid = HybridRetriever(
        embedder,
        qdrant,
        bm25_store or BM25Store(products),
        settings,
    )
    return NomenclatureMatcher(
        embedder,
        qdrant,
        settings,
        reranker=DeepSeekReranker(settings),
        hybrid_retriever=hybrid,
    )


def _run_queries(products, settings: Settings, queries: list[str], workers: int):
    if workers <= 1:
        matcher = _build_matcher(products, settings)
        return matcher.match_many_hybrid_with_rerank(queries)

    shared_bm25 = BM25Store(products)
    state = threading.local()

    def match_one(query: str):
        matcher = getattr(state, "matcher", None)
        if matcher is None:
            matcher = _build_matcher(products, settings, bm25_store=shared_bm25)
            state.matcher = matcher
        return matcher.match_one_hybrid_with_rerank(query)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="competitor-eval") as pool:
        return list(pool.map(match_one, queries))


def _bucket(size: int) -> str:
    if size == 1:
        return "1"
    if size <= 3:
        return "2-3"
    if size <= 7:
        return "4-7"
    return "8+"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate competitor-name -> any acceptable LD analogue mapping. "
            "A case passes when the top-1 returned LD id belongs to the grouped "
            "acceptable LD set. Completeness is intentionally not evaluated."
        )
    )
    parser.add_argument("--mapping", required=True, help="Path to mapping_results.csv")
    parser.add_argument(
        "--csv",
        default=str(ROOT / "ld_products_full_nomenclature.csv"),
        help="LD catalog CSV used by the matcher.",
    )
    parser.add_argument(
        "--output",
        default=str(ROOT / "data" / "competitor_analog_hit_any_eval.json"),
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if args.workers <= 0:
        raise SystemExit("--workers must be > 0")
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be > 0")

    cases = load_cases(Path(args.mapping))
    if args.limit is not None:
        cases = cases[: args.limit]

    products = load_products_from_csv(args.csv)
    settings = Settings()
    results = _run_queries(products, settings, [case["query"] for case in cases], args.workers)

    rows = []
    verdicts = Counter()
    bucket_totals = Counter()
    bucket_pass = Counter()

    for case, result in zip(cases, results, strict=True):
        returned_ld_id = result.ld_product.ld_id if result.ld_product is not None else None
        acceptable = set(case["acceptable_ld_ids"])

        if result.status == "MATCHED" and returned_ld_id in acceptable:
            verdict = "PASS"
        elif result.status == "MATCHED":
            verdict = "FAIL_WRONG_LD"
        elif result.status == "NOT_FOUND":
            verdict = "FAIL_NOT_FOUND"
        else:
            verdict = "FAIL_PIPELINE"

        verdicts[verdict] += 1
        bucket = _bucket(case["acceptable_count"])
        bucket_totals[bucket] += 1
        if verdict == "PASS":
            bucket_pass[bucket] += 1

        rows.append(
            {
                **case,
                "actual_status": result.status,
                "returned_ld_id": returned_ld_id,
                "verdict": verdict,
                "returned_is_acceptable": bool(returned_ld_id in acceptable if returned_ld_id else False),
            }
        )

    total = len(rows)
    passed = verdicts["PASS"]
    summary = {
        "evaluated_queries": total,
        "hit_any_rate": passed / total if total else None,
        "pass": passed,
        "fail_wrong_ld": verdicts["FAIL_WRONG_LD"],
        "fail_not_found": verdicts["FAIL_NOT_FOUND"],
        "fail_pipeline": verdicts["FAIL_PIPELINE"],
        "scoring": "top1 returned LD id must be in acceptable_ld_ids; completeness is not evaluated",
        "acceptable_count_buckets": {
            bucket: {
                "n": bucket_totals[bucket],
                "pass": bucket_pass[bucket],
                "hit_any_rate": (
                    bucket_pass[bucket] / bucket_totals[bucket]
                    if bucket_totals[bucket]
                    else None
                ),
            }
            for bucket in ("1", "2-3", "4-7", "8+")
        },
    }

    payload = {
        "mapping": str(Path(args.mapping).resolve()),
        "summary": summary,
        "cases": rows,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"saved: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
