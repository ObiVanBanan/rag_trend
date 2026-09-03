from .models import MatchResult, SearchCandidate, SelectedMatch


class NomenclatureMatcher:
    def __init__(self, embedder, store, settings, reranker=None):
        self.embedder, self.store, self.settings = embedder, store, settings
        self.reranker = reranker

    def _normalize_query(self, query: str) -> str:
        return " ".join(query.split())

    def _search_candidates(self, query: str, limit: int) -> list[SearchCandidate]:
        hits = self.store.search(self.embedder.embed_query(query), limit)
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
            )
            for hit in hits
        ]

    def match_one(self, query: str) -> MatchResult:
        query = self._normalize_query(query)
        if not query:
            return MatchResult(query=query, status="NOT_FOUND")
        candidates = self._search_candidates(query, self.settings.match_top_k)
        best = candidates[0] if candidates else None
        status = "MATCHED" if best and best.score >= self.settings.match_score_threshold else "NOT_FOUND"
        return MatchResult(query=query, status=status, score=best.score if best else None, ld_product=best if status == "MATCHED" else None, candidates=candidates)

    def match_one_with_rerank(self, query: str) -> MatchResult:
        query = self._normalize_query(query)
        if not query:
            return MatchResult(query=query, status="NOT_FOUND")
        candidates = self._search_candidates(query, self.settings.rerank_candidate_limit)
        if not candidates:
            return MatchResult(query=query, status="NOT_FOUND", candidates=[])
        if self.reranker is None:
            raise ValueError("Reranker is not configured")
        try:
            rerank_result = self.reranker.rerank(query, candidates)
        except Exception as exc:
            return MatchResult(
                query=query,
                status="RERANK_FAILED",
                score=candidates[0].score,
                ld_product=candidates[0],
                candidates=candidates,
                reason=str(exc),
            )
        selected = []
        for item in rerank_result.selected:
            candidate = candidates[item.candidate_id - 1]
            selected.append(
                SelectedMatch(
                    candidate_id=item.candidate_id,
                    article=candidate.article,
                    name=candidate.name,
                    vector_score=candidate.score,
                    llm_confidence=item.confidence,
                    reason=item.reason,
                    ld_id=candidate.ld_id,
                    dn=candidate.dn,
                    pn=candidate.pn,
                    joining_type=candidate.joining_type,
                    url=candidate.url,
                )
            )
        best = candidates[rerank_result.selected[0].candidate_id - 1] if rerank_result.selected else None
        return MatchResult(
            query=query,
            status=rerank_result.status,
            score=best.score if best else None,
            ld_product=best if rerank_result.status == "MATCHED" else None,
            candidates=candidates,
            selected=selected,
            reason=rerank_result.reason,
        )

    def match_many(self, queries: list[str]) -> list[MatchResult]:
        cache = {q: self.match_one(q) for q in dict.fromkeys(queries) if q.strip()}
        return [cache.get(q, MatchResult(query=self._normalize_query(q), status="NOT_FOUND")) for q in queries]

    match = match_many
