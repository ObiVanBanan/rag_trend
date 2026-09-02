import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from nomenclature_matcher.embeddings import OpenAIEmbedder
from nomenclature_matcher.matcher import NomenclatureMatcher
from nomenclature_matcher.qdrant_store import QdrantStore
from nomenclature_matcher.settings import Settings

parser = argparse.ArgumentParser()
parser.add_argument("query")
args = parser.parse_args()
result = NomenclatureMatcher(OpenAIEmbedder(Settings()), QdrantStore(Settings()), Settings()).match_one(args.query)
print(f"QUERY:\n{args.query}\n\nSTATUS: {result.status}")
for index, candidate in enumerate(result.candidates, 1):
    print(f"\n{index}.\nscore: {candidate.score}\narticle: {candidate.article}\nname: {candidate.name}\nDN: {candidate.dn}\nPN: {candidate.pn}")
