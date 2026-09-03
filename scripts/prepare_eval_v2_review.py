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


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    queries_path = root / "data" / "eval_queries_v2.json"
    output_path = root / "data" / "eval_v2_review_candidates.json"

    queries = json.loads(queries_path.read_text(encoding="utf-8"))
    products = load_products_from_csv(root / "ld_products_full_nomenclature.csv")
    settings = Settings()
    embedder = OpenAIEmbedder(settings)
    qdrant_store = QdrantStore(settings)
    bm25_store = BM25Store(products)
    hybrid_retriever = HybridRetriever(embedder, qdrant_store, bm25_store, settings)
    matcher = NomenclatureMatcher(embedder, qdrant_store, settings, hybrid_retriever=hybrid_retriever)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "queries": [],
    }

    for item in queries:
        query = item["query"]
        dense_top10 = matcher._search_candidates(query, 10)
        bm25_top10 = hybrid_retriever.search_bm25(query, 10)
        hybrid_top10 = hybrid_retriever.search(query, 10)
        report["queries"].append(
            {
                "id": item["id"],
                "query": query,
                "candidates": merge_review_candidates(dense_top10, bm25_top10, hybrid_top10),
            }
        )

    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved review candidates to {output_path}")
    print(f"Queries: {len(report['queries'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
