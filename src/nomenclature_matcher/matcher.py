import json
import logging
from dataclasses import replace

from .models import LDProduct, MatchResult, SearchCandidate, SelectedMatch
from .query_canonicalization import build_constraint_rendered_query, canonicalize_retrieval_query
from .query_constraints import QueryConstraints, evaluate_product
from .query_signals import explicit_technical_signal_count, has_product_identity

logger = logging.getLogger(__name__)


class NomenclatureMatcher:
    def __init__(
        self,
        embedder,
        store,
        settings,
        reranker=None,
        hybrid_retriever=None,
        query_interpreter=None,
        competitor_lookup=None,
    ):
        self.embedder, self.store, self.settings = embedder, store, settings
        self.reranker = reranker
        self.hybrid_retriever = hybrid_retriever
        if query_interpreter is None and getattr(settings, "query_interpreter_enabled", False):
            from .query_interpreter import DeepSeekQueryInterpreter

            query_interpreter = DeepSeekQueryInterpreter(settings)
        self.query_interpreter = query_interpreter

        if competitor_lookup is None and getattr(settings, "web_search_enabled", False):
            from .web_search_mcp import MCPWebSearchLookup

            competitor_lookup = MCPWebSearchLookup(settings)
        elif competitor_lookup is None and getattr(settings, "competitor_lookup_enabled", False):
            from .competitor_lookup import LocalCompetitorLookup

            competitor_lookup = LocalCompetitorLookup(settings)
        self.competitor_lookup = competitor_lookup

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

    def _second_chance_candidates(
        self,
        constraints: dict,
    ) -> tuple[list[SearchCandidate], list[tuple[int, SearchCandidate]]] | None:
        """Retrieve again in catalog vocabulary without relaxing hard constraints."""
        if self.hybrid_retriever is None:
            return None
        try:
            parsed_constraints = QueryConstraints.model_validate(constraints)
            rendered_query = build_constraint_rendered_query(parsed_constraints)
            if rendered_query is None:
                return None
            fresh_candidates = self.hybrid_retriever.search(
                rendered_query,
                self.settings.hybrid_rerank_limit,
            )
            if not fresh_candidates:
                return None
            indexed_candidates = self._eligible_candidates(fresh_candidates, constraints)
            if not indexed_candidates:
                return None
            return fresh_candidates, indexed_candidates
        except Exception:
            # Strictly additive fallback: any failure preserves the original
            # HARD_CONSTRAINT_FILTER NOT_FOUND behavior.
            return None

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

        candidate_pool = candidates
        indexed_candidates = list(enumerate(candidate_pool, 1))
        if constraints is not None:
            indexed_candidates = self._eligible_candidates(candidate_pool, constraints)
            if not indexed_candidates:
                second_chance = self._second_chance_candidates(constraints)
                if second_chance is None:
                    return MatchResult(
                        query=query,
                        status="NOT_FOUND",
                        candidates=candidate_pool,
                        reason="HARD_CONSTRAINT_FILTER: no retrieved candidate satisfies all QUERY_CONSTRAINTS",
                        query_interpretation=query_interpretation,
                    )
                candidate_pool, indexed_candidates = second_chance

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
                candidates=candidate_pool,
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
            candidates=candidate_pool,
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

    def _lookup_competitor(self, query: str):
        if self.competitor_lookup is None:
            return None, None, None
        try:
            result = self.competitor_lookup.lookup(query)
            return result, result.prompt_context(), result.debug_payload()
        except Exception as exc:  # enrichment must never break matching
            debug = {
                "attempted": True,
                "accepted": False,
                "reason": f"lookup_error:{type(exc).__name__}:{exc}",
                "search_results": "",
                "pages": [],
            }
            return None, None, debug

    def _log_lookup_debug(self, query: str, debug: dict) -> None:
        """Log the web-enrichment evidence so it is visible in Loki/Grafana."""
        try:
            payload = dict(debug)
            payload["input_query"] = query
            pages = payload.get("pages") or []
            trimmed = []
            for page in pages:
                entry = dict(page)
                text = entry.get("text") or ""
                entry["text_chars_total"] = len(text)
                entry["text"] = text[:1200]
                trimmed.append(entry)
            payload["pages"] = trimmed
            payload["search_results"] = (payload.get("search_results") or "")[:1200]
            logger.info("web_enrichment_debug %s", json.dumps(payload, ensure_ascii=False))
        except Exception:
            logger.debug("failed to log enrichment debug", exc_info=True)

    def _log_attributes(self, query: str, interpretation_payload: dict) -> None:
        """Log the attributes extracted from the query (and web evidence)."""
        try:
            payload = {
                "input_query": query,
                "attributes": interpretation_payload.get("constraints", {}),
                "hard_constraints": interpretation_payload.get("hard_constraints", {}),
            }
            if "pre_enrichment_interpretation" in interpretation_payload:
                payload["attributes_before_web"] = interpretation_payload[
                    "pre_enrichment_interpretation"
                ].get("constraints", {})
            logger.info(
                "web_enrichment_attributes %s", json.dumps(payload, ensure_ascii=False)
            )
        except Exception:
            logger.debug("failed to log extracted attributes", exc_info=True)

    def _enrichment_gate(self, query: str, interpretation) -> tuple[bool, str]:
        if self.competitor_lookup is None:
            return False, "enrichment_disabled"
        constraints = interpretation.constraints
        if constraints.catalog_scope == "out_of_scope":
            return False, "pre_enrichment_out_of_scope"
        if constraints.ambiguous:
            return False, "pre_enrichment_ambiguous"
        if not interpretation.searchable and constraints.catalog_scope != "uncertain":
            return False, "pre_enrichment_not_searchable"
        if not has_product_identity(query):
            return False, "pre_enrichment_no_product_identity"
        # Rich queries already contain enough explicit facts; web would mostly add
        # redundant attributes and latency while increasing false hard constraints.
        if explicit_technical_signal_count(query) >= 4:
            return False, "pre_enrichment_enough_explicit_detail"
        return True, "pre_enrichment_eligible"

    @staticmethod
    def _hard_constraints(pre_interpretation, enriched_interpretation=None) -> dict:
        """Keep web-derived characteristics soft for retrieval, not hard filtering."""
        hard = pre_interpretation.constraints.model_dump()
        if enriched_interpretation is not None:
            enriched = enriched_interpretation.constraints
            # Product family is the only web-derived field allowed to become hard,
            # and only when the first pass could not identify a supported family.
            if hard.get("product_type") == "other" and enriched.product_type != "other":
                hard["product_type"] = enriched.product_type
        return hard

    def match_one_hybrid_with_rerank(self, query: str) -> MatchResult:
        query = self._normalize_query(query)
        if not query:
            return MatchResult(query=query, status="NOT_FOUND")
        if self.hybrid_retriever is None:
            raise ValueError("Hybrid retriever is not configured")

        if self.query_interpreter is not None:
            try:
                pre_interpretation = self.query_interpreter.interpret(query)
            except Exception as exc:
                return MatchResult(
                    query=query,
                    status="RERANK_FAILED",
                    reason=f"QUERY_INTERPRET_FAILED: {type(exc).__name__}: {exc}",
                )

            interpretation = pre_interpretation
            lookup_debug = None
            enriched_interpretation = None
            should_enrich, gate_reason = self._enrichment_gate(query, pre_interpretation)

            if should_enrich:
                _, competitor_context, lookup_debug = self._lookup_competitor(query)
                if competitor_context is not None:
                    try:
                        enriched_interpretation = self.query_interpreter.interpret(
                            query,
                            competitor_context=competitor_context,
                        )
                        interpretation = enriched_interpretation
                    except Exception as exc:
                        # Web enrichment is optional. If the second pass fails, retain
                        # the valid first-pass interpretation instead of failing matching.
                        if lookup_debug is None:
                            lookup_debug = {}
                        lookup_debug["enrichment_interpret_error"] = (
                            f"{type(exc).__name__}: {exc}"
                        )
                elif lookup_debug is None:
                    lookup_debug = {
                        "attempted": False,
                        "accepted": False,
                        "reason": "enrichment_returned_no_context",
                    }
            elif self.competitor_lookup is not None:
                lookup_debug = {
                    "attempted": False,
                    "accepted": False,
                    "reason": gate_reason,
                }
            if lookup_debug is not None:
                self._log_lookup_debug(query, lookup_debug)

            hard_constraints = self._hard_constraints(
                pre_interpretation,
                enriched_interpretation,
            )
            interpretation_payload = interpretation.model_dump()
            interpretation_payload["hard_constraints"] = hard_constraints
            if enriched_interpretation is not None:
                interpretation_payload["pre_enrichment_interpretation"] = (
                    pre_interpretation.model_dump()
                )
            if lookup_debug is not None:
                interpretation_payload["competitor_lookup"] = lookup_debug

            self._log_attributes(query, interpretation_payload)

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
                constraints=hard_constraints,
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
