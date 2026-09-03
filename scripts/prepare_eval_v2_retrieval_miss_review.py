from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.bm25_store import BM25Store
from nomenclature_matcher.documents import load_products_from_csv
from nomenclature_matcher.embeddings import OpenAIEmbedder
from nomenclature_matcher.eval_v2_ground_truth import build_expanded_review_report
from nomenclature_matcher.hybrid_retriever import HybridRetriever
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.qdrant_store import QdrantStore
from nomenclature_matcher.review_state import load_review_state
from nomenclature_matcher.settings import Settings


ROOT = Path(__file__).resolve().parents[1]
BASE_CANDIDATES_PATH = ROOT / "data" / "eval_v2_review_candidates.json"
BASE_REVIEW_STATE_PATH = ROOT / "data" / "eval_v2_human_review.json"
OUTPUT_PATH = ROOT / "data" / "eval_v2_retrieval_miss_candidates.json"


def main() -> int:
    candidates_payload = json.loads(BASE_CANDIDATES_PATH.read_text(encoding="utf-8"))
    review_state = load_review_state(BASE_REVIEW_STATE_PATH)

    settings = Settings()
    products = load_products_from_csv(ROOT / "ld_products_full_nomenclature.csv")
    embedder = OpenAIEmbedder(settings)
    qdrant_store = QdrantStore(settings)
    bm25_store = BM25Store(products)
    hybrid_retriever = HybridRetriever(embedder, qdrant_store, bm25_store, settings)
    matcher = NomenclatureMatcher(embedder, qdrant_store, settings, hybrid_retriever=hybrid_retriever)

    report = build_expanded_review_report(
        candidates_payload,
        review_state,
        lambda query, limit: matcher._search_candidates(query, limit),
        lambda query, limit: hybrid_retriever.search_bm25(query, limit),
        lambda query, limit: hybrid_retriever.search(query, limit),
        max_per_source=100,
    )
    OUTPUT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved expanded retrieval-miss candidates to {OUTPUT_PATH}")
    print(f"Queries: {len(report['queries'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
