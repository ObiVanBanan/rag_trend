import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from nomenclature_matcher.embeddings import OpenAIEmbedder
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.qdrant_store import QdrantStore
from nomenclature_matcher.reranker import DeepSeekReranker
from nomenclature_matcher.settings import Settings

parser = argparse.ArgumentParser()
parser.add_argument("query")
parser.add_argument("--rerank", action="store_true")
args = parser.parse_args()
settings = Settings()
matcher = NomenclatureMatcher(
    OpenAIEmbedder(settings),
    QdrantStore(settings),
    settings,
    reranker=DeepSeekReranker(settings) if args.rerank else None,
)
result = matcher.match_one_with_rerank(args.query) if args.rerank else matcher.match_one(args.query)
print(f"QUERY:\n{args.query}")
if args.rerank:
    print("\nQDRANT TOP-20")
else:
    print(f"\nSTATUS: {result.status}")
for index, candidate in enumerate(result.candidates, 1):
    print(f"\n{index}.\nscore: {candidate.score}\narticle: {candidate.article}\nname: {candidate.name}\nDN: {candidate.dn}\nPN: {candidate.pn}")
if args.rerank:
    print(f"\n\nDEEPSEEK RESULT\n\nSTATUS: {result.status}")
    if result.reason:
        print(f"reason: {result.reason}")
    for item in result.selected:
        print(f"\n{item.candidate_id}.\ncandidate_id: {item.candidate_id}\narticle: {item.article}\nname: {item.name}\nvector_score: {item.vector_score}\nllm_confidence: {item.llm_confidence}\n\nreason:\n{item.reason}")
