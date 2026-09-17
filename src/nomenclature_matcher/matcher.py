from dataclasses import replace

from .models import LDProduct, MatchResult, SearchCandidate, SelectedMatch
from .query_canonicalization import canonicalize_retrieval_query
from .query_constraints import QueryConstraints, evaluate_product


class NomenclatureMatcher:
    def __init__(
        self,
        embedder,
        store,
        settings,
        reranker=None,
        hybrid_retriever=None,
        query_interpreter=None,
    ):
        self.embedder, self.store, self.settings = embedder, store, settings
        self.reranker = reranker
        self.hybrid_retriever = hybrid_retriever
        if query_interpreter is None and getattr(settings, "query_interpreter_enabled", False):
            from .query_interpreter import DeepSeekQueryInterpreter

            query_interpreter = DeepSeekQueryInterpreter(settings)
        self.query_interpreter = query_interpreter

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
                dense_score=float(hit.score),
                dense_rank=index,
                retrieval_sources=["dense"],
            )
            for index, hit in enumerate(hits, 1)
        ]

    @staticmethod
    def _candidate_as_product(candidate: SearchCandidate) -> LDProduct:
        return LDProduct(
            id=candidate.ld_id,
            name=candidate.name,
            article=candidate.article,
            price=candidate.price,
            dn=candidate.dn,
            pn=candidate.pn,
            joining_type=candidate.joining_type,
            url=candidate.url,
            properties=candidate.properties or [],
        )

    def _eligible_candidates(
        self,
        candidates: list[SearchCandidate],
        constraints: dict,
    ) -> list[tuple[int, SearchCandidate]]:
        parsed = QueryConstraints.model_validate(constraints)
        return [
            (index, candidate)
            for index, candidate in enumerate(candidates, 1)
            if evaluate_product(self._candidate_as_product(candidate), parsed).matches
        ]

    def _build_selected_match(
        self,
        candidate: SearchCandidate,
        item,
        *,
        candidate_id: int | None = None,
    ) -> SelectedMatch:
        return SelectedMatch(
            candidate_id=candidate_id if candidate_id is not None else item.candidate_id,
            article=candidate.article,
            name=candidate.name,
            llm_confidence=item.confidence,
            reason=item.reason,
            ld_id=candidate.ld_id,
            dense_score=candidate.dense_score,
            bm25_score=candidate.bm25_score,
            rrf_score=candidate.rrf_score,
            dn=candidate.dn,
            pn=candidate.pn,
            joining_type=candidate.joining_type,
            url=candidate.url,
        )

    def rerank_candidates(
        self,
        query: str,
        candidates: list[SearchCandidate],
        *,
        constraints: dict | None = None,
        query_interpretation: dict | None = None,
    ) -> MatchResult:
        if not candidates:
            return MatchResult(
                query=query,
                status="NOT_FOUND",
                candidates=[],
                query_interpretation=query_interpretation,
            )
        if self.reranker is None:
            raise ValueError("Reranker is not configured")

        indexed_candidates = list(enumerate(candidates, 1))
        if constraints is not None:
            indexed_candidates = self._eligible_candidates(candidates, constraints)
            if not indexed_candidates:
                return MatchResult(
                    query=query,
                    status="NOT_FOUND",
                    candidates=candidates,
                    reason="HARD_CONSTRAINT_FILTER: no retrieved candidate satisfies all QUERY_CONSTRAINTS",
                    query_interpretation=query_interpretation,
                )

        rerank_input = [candidate for _, candidate in indexed_candidates]
        try:
            if constraints is None:
                rerank_result = self.reranker.rerank(query, rerank_input)
            else:
                rerank_result = self.reranker.rerank(query, rerank_input, constraints=constraints)
        except Exception as exc:
            return MatchResult(
                query=query,
                status="RERANK_FAILED",
                score=candidates[0].score,
                ld_product=None,
                candidates=candidates,
                reason=str(exc),
                query_interpretation=query_interpretation,
            )

        selected = []
        selected_candidates: list[SearchCandidate] = []
        for item in rerank_result.selected:
            original_candidate_id, candidate = indexed_candidates[item.candidate_id - 1]
            selected.append(
                self._build_selected_match(
                    candidate,
                    item,
                    candidate_id=original_candidate_id,
                )
            )
            selected_candidates.append(candidate)

        best = selected_candidates[0] if selected_candidates else None
        return MatchResult(
            query=query,
            status=rerank_result.status,
            score=best.score if best else None,
            ld_product=best if rerank_result.status == "MATCHED" else None,
            candidates=candidates,
            selected=selected,
            reason=rerank_result.reason,
            query_interpretation=query_interpretation,
        )

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
        return self.rerank_candidates(query, candidates)

    def match_one_hybrid_with_rerank(self, query: str) -> MatchResult:
        query = self._normalize_query(query)
        if not query:
            return MatchResult(query=query, status="NOT_FOUND")
        if self.hybrid_retriever is None:
            raise ValueError("Hybrid retriever is not configured")

        if self.query_interpreter is not None:
            try:
                interpretation = self.query_interpreter.interpret(query)
            except Exception as exc:
                return MatchResult(
                    query=query,
                    status="RERANK_FAILED",
                    reason=f"QUERY_INTERPRET_FAILED: {type(exc).__name__}: {exc}",
                )

            interpretation_payload = interpretation.model_dump()
            if not interpretation.searchable:
                return MatchResult(
                    query=query,
                    status="NOT_FOUND",
                    reason=f"QUERY_REJECTED: {interpretation.reason}",
                    query_interpretation=interpretation_payload,
                )

            normalized_query = self._normalize_query(interpretation.normalized_query) or query
            canonical_query = normalized_query if normalized_query != query else None
            candidates = self.hybrid_retriever.search(
                query,
                self.settings.hybrid_rerank_limit,
                canonical_query=canonical_query,
            )
            return self.rerank_candidates(
                query,
                candidates,
                constraints=interpretation.constraints.model_dump(),
                query_interpretation=interpretation_payload,
            )

        canonicalization = canonicalize_retrieval_query(query)
        candidates = self.hybrid_retriever.search(
            query,
            self.settings.hybrid_rerank_limit,
            canonical_query=canonicalization.canonical_query,
        )
        return self.rerank_candidates(query, candidates)

    def _match_many_with(self, queries: list[str], match_one) -> list[MatchResult]:
        cache = {}
        for query in queries:
            normalized = self._normalize_query(query)
            if normalized and normalized not in cache:
                cache[normalized] = match_one(normalized)
        return [
            replace(cache[normalized], query=query)
            if (normalized := self._normalize_query(query)) and normalized in cache
            else MatchResult(query=query, status="NOT_FOUND")
            for query in queries
        ]

    def match_many(self, queries: list[str]) -> list[MatchResult]:
        return self._match_many_with(queries, self.match_one)

    def match_many_hybrid_with_rerank(self, queries: list[str]) -> list[MatchResult]:
        return self._match_many_with(queries, self.match_one_hybrid_with_rerank)

    match = match_many
