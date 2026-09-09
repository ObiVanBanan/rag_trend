import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from nomenclature_matcher.documents import load_products_from_csv
from nomenclature_matcher.embeddings import OpenAIEmbedder
from nomenclature_matcher.indexer import build_index
from nomenclature_matcher.qdrant_store import QdrantStore
from nomenclature_matcher.settings import Settings


if os.environ.get("RAG_HARNESS_AGENT") == "1":
    raise SystemExit(
        "Full index rebuilds are owned by the outer RAG harness. "
        "Return needs_reindex=true instead of running scripts/build_index.py from an agent role."
    )

parser = argparse.ArgumentParser()
parser.add_argument("--csv", required=True)
args = parser.parse_args()
settings = Settings()
products = load_products_from_csv(args.csv)
print(f"Loaded products: {len(products)}")
print(f"Indexed products: {build_index(products, OpenAIEmbedder(settings), QdrantStore(settings))}")
