from __future__ import annotations

import math
from collections import Counter, defaultdict

from .documents import build_lexical_text, build_search_text, tokenize
from .models import BM25Candidate, LDProduct

try:  # pragma: no cover - exercised when dependency is installed
    from rank_bm25 import BM25Okapi
except ImportError:  # pragma: no cover - lightweight fallback for local runs
    class BM25Okapi:
        def __init__(self, corpus, k1: float = 1.5, b: float = 0.75):
            self.corpus = corpus
            self.k1 = k1
            self.b = b
            self.doc_freqs = [Counter(document) for document in corpus]
            self.doc_len = [len(document) for document in corpus]
            self.avgdl = sum(self.doc_len) / len(self.doc_len) if self.doc_len else 0.0
            self.doc_count = len(corpus)
            doc_freq = defaultdict(int)
            for freqs in self.doc_freqs:
                for token in freqs:
                    doc_freq[token] += 1
            self.idf = {
                token: math.log(1 + (self.doc_count - freq + 0.5) / (freq + 0.5))
                for token, freq in doc_freq.items()
            }

        def get_scores(self, query_tokens):
            if not self.corpus:
                return []
            scores = [0.0] * self.doc_count
            for token in query_tokens:
                idf = self.idf.get(token)
                if idf is None:
                    continue
                for index, freqs in enumerate(self.doc_freqs):
                    freq = freqs.get(token, 0)
                    if not freq:
                        continue
                    denom = freq + self.k1 * (1 - self.b + self.b * (self.doc_len[index] / self.avgdl if self.avgdl else 0.0))
                    scores[index] += idf * (freq * (self.k1 + 1)) / denom
            return scores


class BM25Store:
    def __init__(self, products: list[LDProduct]):
        self.products = list(products)
        self.lexical_texts = [build_lexical_text(product) for product in self.products]
        self.search_texts = [build_search_text(product) for product in self.products]
        self.corpus = [tokenize(text) for text in self.lexical_texts]
        self.model = BM25Okapi(self.corpus)

    def search(self, query: str, limit: int) -> list[BM25Candidate]:
        query_tokens = tokenize(query)
        if not query_tokens or not self.products or limit <= 0:
            return []
        scores = self.model.get_scores(query_tokens)
        ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)[:limit]
        results: list[BM25Candidate] = []
        for index, score in ranked:
            if score <= 0:
                continue
            product = self.products[index]
            results.append(
                BM25Candidate(
                    ld_id=product.id,
                    name=product.name,
                    article=product.article,
                    bm25_score=float(score),
                    price=product.price,
                    dn=product.dn,
                    pn=product.pn,
                    joining_type=product.joining_type,
                    url=product.url,
                    properties=product.properties if isinstance(product.properties, list) else None,
                    search_text=self.search_texts[index],
                )
            )
        return results
