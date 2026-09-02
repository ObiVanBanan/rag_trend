from qdrant_client import models
from .documents import build_search_text


def build_index(products, embedder, store):
    store.ensure_collection()
    texts = [build_search_text(product) for product in products]
    vectors = embedder.embed_documents(texts)
    indexed = 0
    batch_size = getattr(embedder.settings, "dense_batch_size", 32)
    for start in range(0, len(products), batch_size):
        batch = [(product.id, vector, {"ld_id": product.id, "name": product.name, "article": product.article, "price": product.price, "dn": product.dn, "pn": product.pn, "joining_type": product.joining_type, "url": product.url, "search_text": text})
                 for product, vector, text in zip(products[start:start + batch_size], vectors[start:start + batch_size], texts[start:start + batch_size])]
        store.upsert(batch)
        indexed += len(batch)
        print(f"Indexed: {indexed}/{len(products)}")
    return indexed
