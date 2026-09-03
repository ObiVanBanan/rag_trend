from __future__ import annotations

from .bm25_store import BM25Store
from .models import SearchCandidate


class HybridRetriever:
    def __init__(self, embedder, qdrant_store, bm25_store: BM25Store, settings):
        self.embedder = embedder
        self.qdrant_store = qdrant_store
        self.bm25_store = bm25_store
        self.settings = settings

    def search_dense(self, query: str, limit: int | None = None) -> list[SearchCandidate]:
        limit = limit or self.settings.hybrid_dense_limit
        hits = self.qdrant_store.search(self.embedder.embed_query(query), limit)
        return [
            SearchCandidate(
                ld_id=int(hit.payload["ld_id"]),
                name=hit.payload.get("name", ""),
                article=hit.payload.get("article"),
                score=float(hit.score),
                price=hit.payload.get("price"),
                dn=hit.payload.get("dn"),
                pn=hit.payload.get("pn"),
                joining_type=hit.payload.get("joining_type"),
                url=hit.payload.get("url"),
                properties=hit.payload.get("properties"),
                search_text=hit.payload.get("search_text"),
                dense_score=float(hit.score),
                dense_rank=index,
                retrieval_sources=["dense"],
            )
            for index, hit in enumerate(hits, 1)
        ]

    def search_bm25(self, query: str, limit: int | None = None) -> list[SearchCandidate]:
        limit = limit or self.settings.hybrid_bm25_limit
        hits = self.bm25_store.search(query, limit)
        return [
            SearchCandidate(
                ld_id=hit.ld_id,
                name=hit.name,
                article=hit.article,
                score=float(hit.bm25_score),
                price=hit.price,
                dn=hit.dn,
                pn=hit.pn,
                joining_type=hit.joining_type,
                url=hit.url,
                properties=hit.properties,
                search_text=hit.search_text,
                bm25_score=float(hit.bm25_score),
                bm25_rank=index,
                retrieval_sources=["bm25"],
            )
            for index, hit in enumerate(hits, 1)
        ]

    def search(self, query: str, limit: int | None = None) -> list[SearchCandidate]:
        limit = limit or self.settings.hybrid_rerank_limit
        dense_candidates = self.search_dense(query, self.settings.hybrid_dense_limit)
        bm25_candidates = self.search_bm25(query, self.settings.hybrid_bm25_limit)
        merged = self._merge_candidates(dense_candidates, bm25_candidates)
        self._apply_rrf(merged)
        return sorted(merged.values(), key=lambda candidate: candidate.rrf_score or 0.0, reverse=True)[:limit]

    def _merge_candidates(
        self,
        dense_candidates: list[SearchCandidate],
        bm25_candidates: list[SearchCandidate],
    ) -> dict[int, SearchCandidate]:
        merged: dict[int, SearchCandidate] = {}
        for candidate in dense_candidates:
            merged[candidate.ld_id] = candidate
        for candidate in bm25_candidates:
            existing = merged.get(candidate.ld_id)
            if existing is None:
                merged[candidate.ld_id] = candidate
                continue
            merged[candidate.ld_id] = self._merge_pair(existing, candidate)
        return merged

    def _merge_pair(self, dense_candidate: SearchCandidate, bm25_candidate: SearchCandidate) -> SearchCandidate:
        sources = list(dict.fromkeys([*dense_candidate.retrieval_sources, *bm25_candidate.retrieval_sources]))
        return SearchCandidate(
            ld_id=dense_candidate.ld_id,
            name=dense_candidate.name or bm25_candidate.name,
            article=dense_candidate.article or bm25_candidate.article,
            score=dense_candidate.score if dense_candidate.score is not None else bm25_candidate.score,
            price=dense_candidate.price if dense_candidate.price is not None else bm25_candidate.price,
            dn=dense_candidate.dn if dense_candidate.dn is not None else bm25_candidate.dn,
            pn=dense_candidate.pn if dense_candidate.pn is not None else bm25_candidate.pn,
            joining_type=dense_candidate.joining_type if dense_candidate.joining_type is not None else bm25_candidate.joining_type,
            url=dense_candidate.url if dense_candidate.url is not None else bm25_candidate.url,
            properties=dense_candidate.properties if dense_candidate.properties is not None else bm25_candidate.properties,
            search_text=dense_candidate.search_text or bm25_candidate.search_text,
            dense_score=dense_candidate.dense_score,
            dense_rank=dense_candidate.dense_rank,
            bm25_score=bm25_candidate.bm25_score,
            bm25_rank=bm25_candidate.bm25_rank,
            rrf_score=None,
            retrieval_sources=sources,
        )

    def _apply_rrf(self, candidates: dict[int, SearchCandidate]) -> None:
        rrf_k = self.settings.rrf_k
        for candidate in candidates.values():
            score = 0.0
            if candidate.dense_rank is not None:
                score += 1.0 / (rrf_k + candidate.dense_rank)
            if candidate.bm25_rank is not None:
                score += 1.0 / (rrf_k + candidate.bm25_rank)
            candidate.rrf_score = score
            candidate.score = score
