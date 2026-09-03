import argparse
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
from nomenclature_matcher.settings import Settings

parser = argparse.ArgumentParser()
parser.add_argument("query")
parser.add_argument("--mode", choices=["dense", "dense-rerank", "hybrid-rerank"], default="dense")
parser.add_argument("--csv", default=str(Path(__file__).resolve().parents[1] / "ld_products_full_nomenclature.csv"))
args = parser.parse_args()
settings = Settings()
embedder = OpenAIEmbedder(settings)
qdrant_store = QdrantStore(settings)
reranker = DeepSeekReranker(settings) if args.mode in {"dense-rerank", "hybrid-rerank"} else None
hybrid_retriever = None
if args.mode == "hybrid-rerank":
    products = load_products_from_csv(args.csv)
    hybrid_retriever = HybridRetriever(embedder, qdrant_store, BM25Store(products), settings)
matcher = NomenclatureMatcher(
    embedder,
    qdrant_store,
    settings,
    reranker=reranker,
    hybrid_retriever=hybrid_retriever,
)
try:
    if args.mode == "dense":
        result = matcher.match_one(args.query)
    elif args.mode == "dense-rerank":
        result = matcher.match_one_with_rerank(args.query)
    else:
        result = matcher.match_one_hybrid_with_rerank(args.query)
except Exception as exc:
    print(f"ERROR: {exc}", file=sys.stderr)
    raise SystemExit(1)
print(f"QUERY:\n{args.query}")
if args.mode == "dense":
    print(f"\nSTATUS: {result.status}")
elif args.mode == "dense-rerank":
    print("\nQDRANT TOP-20")
else:
    print("\nHYBRID TOP-20")
for index, candidate in enumerate(result.candidates, 1):
    print(
        f"\n{index}.\narticle: {candidate.article}\nname: {candidate.name}\ndense_rank: {candidate.dense_rank if candidate.dense_rank is not None else '-'}"
        f"\ndense_score: {candidate.dense_score if candidate.dense_score is not None else '-'}\nbm25_rank: {candidate.bm25_rank if candidate.bm25_rank is not None else '-'}"
        f"\nbm25_score: {candidate.bm25_score if candidate.bm25_score is not None else '-'}\nrrf_score: {candidate.rrf_score if candidate.rrf_score is not None else candidate.score}"
    )
if args.mode != "dense":
    print(f"\n\nDEEPSEEK RESULT\n\nSTATUS: {result.status}")
    if result.reason:
        print(f"reason: {result.reason}")
    for item in result.selected:
        print(f"\n{item.candidate_id}.\ncandidate_id: {item.candidate_id}\narticle: {item.article}\nname: {item.name}\nvector_score: {item.vector_score}\nllm_confidence: {item.llm_confidence}\n\nreason:\n{item.reason}")
