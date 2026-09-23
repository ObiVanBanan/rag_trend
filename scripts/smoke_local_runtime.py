from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openai import OpenAI

from nomenclature_matcher.embeddings import create_embedder
from nomenclature_matcher.qdrant_store import QdrantStore
from nomenclature_matcher.settings import Settings


def main() -> int:
    settings = Settings()

    print("Local runtime smoke")
    print(f"- embedding_provider: {settings.embedding_provider}")
    print(f"- embedding_model: {settings.embedding_model}")
    print(f"- embedding_dimension: {settings.embedding_dimension}")
    print(f"- ollama_base_url: {settings.ollama_base_url}")
    print(f"- llm_base_url: {settings.deepseek_base_url}")
    print(f"- llm_model: {settings.deepseek_model}")
    print(f"- qdrant_url: {settings.qdrant_url}")
    print(f"- qdrant_collection: {settings.qdrant_collection_alias}")

    if str(settings.embedding_provider).lower() != "ollama":
        raise SystemExit("Expected EMBEDDING_PROVIDER=ollama for the local profile.")
    if "REPLACE_WITH" in settings.deepseek_model:
        raise SystemExit("Set DEEPSEEK_MODEL to the exact LM Studio model id first.")

    embedder = create_embedder(settings)
    vector = embedder.embed_query("Кран шаровой стальной Ду50 Ру16")
    if len(vector) != settings.embedding_dimension:
        raise SystemExit(
            f"Embedding dimension mismatch: {len(vector)} != {settings.embedding_dimension}"
        )
    print(f"OK Ollama embedding: {len(vector)} dims")

    llm = OpenAI(
        api_key=settings.deepseek_api_key or "lm-studio",
        base_url=settings.deepseek_base_url,
        timeout=settings.deepseek_timeout_seconds,
    )
    model_ids = [item.id for item in llm.models.list().data]
    print(f"LM Studio models: {model_ids}")
    if settings.deepseek_model not in model_ids:
        raise SystemExit(
            f"Configured DEEPSEEK_MODEL={settings.deepseek_model!r} is not loaded in LM Studio."
        )

    response = llm.chat.completions.create(
        model=settings.deepseek_model,
        temperature=0,
        messages=[
            {"role": "system", "content": "Return only valid JSON."},
            {"role": "user", "content": 'Return {"ok": true}.'},
        ],
    )
    payload = json.loads(response.choices[0].message.content or "{}")
    if payload.get("ok") is not True:
        raise SystemExit(f"Unexpected LM Studio structured response: {payload!r}")
    print("OK LM Studio chat + JSON mode")

    store = QdrantStore(settings)
    collections = store.client.get_collections()
    print(f"OK Qdrant reachable: {len(collections.collections)} collections")
    if store.client.collection_exists(settings.qdrant_collection_alias):
        print("OK local Qdrant collection already exists")
    else:
        print("INFO local Qdrant collection does not exist yet; build_index will create it")

    print("LOCAL_RUNTIME_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
