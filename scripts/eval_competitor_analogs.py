from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import lzma
import re
import random
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
_EMBEDDED_PARTS_DIR = ROOT / "data" / "competitor_analog_hit_any_min_v1.parts"
_EXPECTED_PARTS = 6
_EXPECTED_CASES = 15885
_EXPECTED_XZ_SHA256 = "36893196c5433a5e74631effd7e79cc8cc922cd2f8b14916cbad0eb311f4c958"
_EXPECTED_TSV_SHA256 = "8fe751d3a0ab014198b4d466c4c698b85fa502bc7e0bc18c8f6a68b339a53faa"


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
    """Group raw mapping_results.csv rows by competitor product name."""
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


def load_embedded_cases(parts_dir: Path = _EMBEDDED_PARTS_DIR) -> list[dict]:
    """Load the frozen grouped benchmark bundled with this branch."""
    part_paths = sorted(parts_dir.glob("part*.b64"))
    if len(part_paths) != _EXPECTED_PARTS:
        raise ValueError(
            f"Embedded benchmark expected {_EXPECTED_PARTS} parts, found {len(part_paths)} "
            f"in {parts_dir}"
        )

    encoded = "".join(path.read_text(encoding="ascii").strip() for path in part_paths)
    try:
        compressed = base64.b64decode(encoded, validate=True)
    except Exception as exc:
        raise ValueError(f"Invalid embedded benchmark base64: {exc}") from exc

    compressed_sha = hashlib.sha256(compressed).hexdigest()
    if compressed_sha != _EXPECTED_XZ_SHA256:
        raise ValueError(
            "Embedded benchmark compressed SHA mismatch: "
            f"{compressed_sha} != {_EXPECTED_XZ_SHA256}"
        )

    try:
        raw = lzma.decompress(compressed)
    except lzma.LZMAError as exc:
        raise ValueError(f"Cannot decompress embedded benchmark: {exc}") from exc

    raw_sha = hashlib.sha256(raw).hexdigest()
    if raw_sha != _EXPECTED_TSV_SHA256:
        raise ValueError(
            f"Embedded benchmark TSV SHA mismatch: {raw_sha} != {_EXPECTED_TSV_SHA256}"
        )

    reader = csv.DictReader(io.StringIO(raw.decode("utf-8")), delimiter="\t")
    required = {"query", "acceptable_ld_ids"}
    missing = required - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"Embedded benchmark misses columns: {sorted(missing)}")

    cases: list[dict] = []
    seen_queries: set[str] = set()
    for row_no, row in enumerate(reader, start=2):
        query = _clean(row["query"])
        if not query:
            raise ValueError(f"Embedded benchmark row {row_no} has empty query")
        normalized = _norm(query)
        if normalized in seen_queries:
            raise ValueError(f"Embedded benchmark has duplicate query at row {row_no}: {query!r}")
        seen_queries.add(normalized)

        acceptable_ids = sorted(
            {
                int(value)
                for value in str(row["acceptable_ld_ids"] or "").split("|")
                if value.strip()
            }
        )
        if not acceptable_ids:
            raise ValueError(f"Embedded benchmark row {row_no} has no acceptable LD ids")

        cases.append(
            {
                "query": query,
                "acceptable_ld_ids": acceptable_ids,
                "acceptable_count": len(acceptable_ids),
            }
        )

    if len(cases) != _EXPECTED_CASES:
        raise ValueError(
            f"Embedded benchmark expected {_EXPECTED_CASES} grouped cases, found {len(cases)}"
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
    parser.add_argument(
        "--mapping",
        help=(
            "Optional raw mapping_results.csv. When omitted, use the frozen "
            "15,885-query benchmark bundled with this branch."
        ),
    )
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
    parser.add_argument("--limit", type=int, default=None, help="Evaluate the first N grouped cases.")
    parser.add_argument("--sample", type=int, default=None, help="Evaluate a deterministic random sample of N grouped cases.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed used with --sample (default: 42).")
    parser.add_argument("--verify-only", action="store_true", help="Validate/load the benchmark and exit before RAG/API calls.")
    args = parser.parse_args()

    if args.workers <= 0:
        raise SystemExit("--workers must be > 0")
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be > 0")
    if args.sample is not None and args.sample <= 0:
        raise SystemExit("--sample must be > 0")
    if args.limit is not None and args.sample is not None:
        raise SystemExit("Use only one of --limit or --sample")

    if args.mapping:
        mapping_path = Path(args.mapping).expanduser().resolve()
        cases = load_cases(mapping_path)
        dataset_source = str(mapping_path)
    else:
        cases = load_embedded_cases()
        dataset_source = "embedded:competitor_analog_hit_any_min_v1"

    total_available = len(cases)
    if args.limit is not None:
        cases = cases[: args.limit]
    elif args.sample is not None:
        if args.sample > total_available:
            raise SystemExit(f"--sample {args.sample} exceeds available cases {total_available}")
        cases = random.Random(args.seed).sample(cases, args.sample)

    if args.verify_only:
        print(json.dumps({
            "status": "ok",
            "dataset_source": dataset_source,
            "available_queries": total_available,
            "selected_queries": len(cases),
            "embedded_tsv_sha256": _EXPECTED_TSV_SHA256 if not args.mapping else None,
            "embedded_xz_sha256": _EXPECTED_XZ_SHA256 if not args.mapping else None,
        }, ensure_ascii=False, indent=2))
        return 0

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
        "dataset_source": dataset_source,
        "available_queries": total_available,
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
        "dataset_source": dataset_source,
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
