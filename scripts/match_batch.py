import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.bm25_store import BM25Store
from nomenclature_matcher.documents import load_products_from_csv
from nomenclature_matcher.embeddings import OpenAIEmbedder
from nomenclature_matcher.hybrid_retriever import HybridRetriever
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.qdrant_store import QdrantStore
from nomenclature_matcher.reranker import DeepSeekReranker
from nomenclature_matcher.serialization import match_results_payload
from nomenclature_matcher.settings import Settings


def _read_queries(path: str | None) -> list[str]:
    raw = Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()
    payload = json.loads(raw)
    if not isinstance(payload, list) or not all(isinstance(item, str) for item in payload):
        raise ValueError("Input must be a JSON array of strings")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", help="Path to JSON array of nomenclature strings. Reads stdin when omitted.")
    parser.add_argument("--csv", default=str(Path(__file__).resolve().parents[1] / "ld_products_full_nomenclature.csv"))
    parser.add_argument("--no-debug", action="store_true", help="Omit debug candidates and LLM details from JSON output.")
    args = parser.parse_args()

    settings = Settings()
    products = load_products_from_csv(args.csv)
    embedder = OpenAIEmbedder(settings)
    qdrant_store = QdrantStore(settings)
    matcher = NomenclatureMatcher(
        embedder,
        qdrant_store,
        settings,
        reranker=DeepSeekReranker(settings),
        hybrid_retriever=HybridRetriever(embedder, qdrant_store, BM25Store(products), settings),
    )
    results = matcher.match_many_hybrid_with_rerank(_read_queries(args.input))
    print(json.dumps(match_results_payload(results, include_debug=not args.no_debug), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
