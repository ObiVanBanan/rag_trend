from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.bm25_store import BM25Store
from nomenclature_matcher.documents import load_products_from_csv
from nomenclature_matcher.embeddings import OpenAIEmbedder
from nomenclature_matcher.eval_v2 import merge_review_candidates
from nomenclature_matcher.hybrid_retriever import HybridRetriever
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.qdrant_store import QdrantStore
from nomenclature_matcher.settings import Settings


def _build_parser() -> argparse.ArgumentParser:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Generate a human-review candidate pool from Dense + BM25 + Hybrid retrieval."
    )
    parser.add_argument("--queries", default=str(root / "data" / "eval_queries.json"))
    parser.add_argument("--output", default=str(root / "data" / "golden_review_candidates.json"))
    parser.add_argument("--csv", default=str(root / "ld_products_full_nomenclature.csv"))
    parser.add_argument("--top-k", type=int, default=20)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    queries_path = Path(args.queries)
    output_path = Path(args.output)
    csv_path = Path(args.csv)
    if args.top_k <= 0:
        raise SystemExit("--top-k must be > 0")

    queries = json.loads(queries_path.read_text(encoding="utf-8"))
    products = load_products_from_csv(csv_path)
    settings = Settings()
    embedder = OpenAIEmbedder(settings)
    qdrant_store = QdrantStore(settings)
    bm25_store = BM25Store(products)
    hybrid_retriever = HybridRetriever(embedder, qdrant_store, bm25_store, settings)
    matcher = NomenclatureMatcher(
        embedder,
        qdrant_store,
        settings,
        hybrid_retriever=hybrid_retriever,
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "queries_path": str(queries_path),
        "catalog_path": str(csv_path),
        "top_k_per_source": args.top_k,
        "retrieval": {
            "dense": True,
            "bm25": True,
            "hybrid_rrf": True,
            "llm_reranker": False,
        },
        "queries": [],
    }

    for index, item in enumerate(queries, 1):
        query = item["query"]
        dense = matcher._search_candidates(query, args.top_k)
        bm25 = hybrid_retriever.search_bm25(query, args.top_k)
        hybrid = hybrid_retriever.search(query, args.top_k)
        merged = merge_review_candidates(
            dense,
            bm25,
            hybrid,
            max_per_source=args.top_k,
        )
        report["queries"].append(
            {
                "id": item["id"],
                "query": query,
                "candidates": merged,
            }
        )
        print(
            f"[{index}/{len(queries)}] {item['id']}: "
            f"dense={len(dense)} bm25={len(bm25)} hybrid={len(hybrid)} union={len(merged)}"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved review candidates to {output_path}")
    print(f"Queries: {len(report['queries'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
