from dataclasses import replace
from time import perf_counter

from .competitor_decoding import decode_competitor_query
from .models import LDProduct, MatchResult, SearchCandidate, SelectedMatch
from .query_canonicalization import build_constraint_rendered_query, canonicalize_retrieval_query
from .query_constraints import (
    QueryConstraints,
    canonical_bore_type,
    canonical_control,
    canonical_material,
    canonical_product_type,
    evaluate_product,
    product_snapshot,
)
from .query_signals import (
    explicit_dn_from_query,
    explicit_joining_type_from_query,
    explicit_pn_mpa_from_query,
    explicit_technical_signal_count,
    explicit_thread_type_from_query,
    explicit_working_medium_from_query,
    has_product_identity,
)
from .observability import (
    build_attribute_diagnostic,
    log_match_trace,
    log_operational_event,
    record_attribute_conflicts,
    record_enrichment_gate,
    record_web_enrichment,
    trim_text,
)


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
        competitor_resolver=None,
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

        if (
            competitor_resolver is None
            and getattr(settings, "competitor_resolver_enabled", False)
        ):
            from .competitor_resolver import CompetitorResolver

            competitor_resolver = CompetitorResolver(settings)
        self.competitor_resolver = competitor_resolver

    def _normalize_query(self, query: str) -> str:
        return " ".join(query.split())

    def _interpret_query(self, query: str, competitor_context=None):
        if competitor_context is None:
            return self.query_interpreter.interpret(query)
        return self.query_interpreter.interpret(
            query,
            competitor_context=competitor_context,
        )

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
        rerank_query: str | None = None,
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
        retrieval_trace = None
        if isinstance(query_interpretation, dict):
            maybe_trace = query_interpretation.get("retrieval_trace")
            if isinstance(maybe_trace, dict):
                retrieval_trace = maybe_trace
                retrieval_trace["initial_candidate_count"] = len(candidate_pool)
                retrieval_trace["initial_candidate_ids"] = [
                    candidate.ld_id for candidate in candidate_pool
                ]
                retrieval_trace["initial_candidates"] = [
                    {
                        "ld_id": candidate.ld_id,
                        "name": candidate.name,
                        "article": candidate.article,
                        "dense_rank": candidate.dense_rank,
                        "bm25_rank": candidate.bm25_rank,
                        "rrf_score": candidate.rrf_score,
                        "retrieval_sources": candidate.retrieval_sources,
                    }
                    for candidate in candidate_pool
                ]
                if getattr(self.settings, "match_trace_detailed_enabled", False):
                    parsed_trace_constraints = (
                        QueryConstraints.model_validate(constraints)
                        if constraints is not None
                        else None
                    )
                    retrieval_trace["initial_candidate_snapshots"] = [
                        {
                            "input_rank": index,
                            "dense_rank": candidate.dense_rank,
                            "bm25_rank": candidate.bm25_rank,
                            "rrf_score": candidate.rrf_score,
                            "retrieval_sources": list(candidate.retrieval_sources),
                            "product": product_snapshot(
                                self._candidate_as_product(candidate),
                                parsed_trace_constraints,
                            ),
                            "matches_hard_constraints": (
                                evaluate_product(
                                    self._candidate_as_product(candidate),
                                    parsed_trace_constraints,
                                ).matches
                                if parsed_trace_constraints is not None
                                else None
                            ),
                        }
                        for index, candidate in enumerate(candidate_pool, 1)
                    ]

        if constraints is not None:
            indexed_candidates = self._eligible_candidates(candidate_pool, constraints)
            if retrieval_trace is not None:
                retrieval_trace["eligible_candidate_count_before_second_chance"] = len(
                    indexed_candidates
                )
                retrieval_trace["eligible_candidate_ids_before_second_chance"] = [
                    candidate.ld_id for _, candidate in indexed_candidates
                ]
            if not indexed_candidates:
                second_chance = self._second_chance_candidates(constraints)
                if second_chance is None:
                    if retrieval_trace is not None:
                        retrieval_trace["second_chance_used"] = False
                        retrieval_trace["rerank_candidate_count"] = 0
                        retrieval_trace["rerank_candidate_ids"] = []
                        retrieval_trace["failure_stage"] = "hard_constraint_filter"
                    return MatchResult(
                        query=query,
                        status="NOT_FOUND",
                        candidates=candidate_pool,
                        reason="HARD_CONSTRAINT_FILTER: no retrieved candidate satisfies all QUERY_CONSTRAINTS",
                        query_interpretation=query_interpretation,
                    )
                candidate_pool, indexed_candidates = second_chance
                if retrieval_trace is not None:
                    retrieval_trace["second_chance_used"] = True
                    retrieval_trace["second_chance_candidate_ids"] = [
                        candidate.ld_id for candidate in candidate_pool
                    ]
                    retrieval_trace["eligible_candidate_ids_after_second_chance"] = [
                        candidate.ld_id for _, candidate in indexed_candidates
                    ]
            elif retrieval_trace is not None:
                retrieval_trace["second_chance_used"] = False

        rerank_input = [candidate for _, candidate in indexed_candidates]
        if retrieval_trace is not None:
            retrieval_trace["rerank_candidate_count"] = len(rerank_input)
            retrieval_trace["rerank_candidate_ids"] = [
                candidate.ld_id for candidate in rerank_input
            ]
        effective_rerank_query = rerank_query or query
        if retrieval_trace is not None:
            retrieval_trace["rerank_query"] = effective_rerank_query
        try:
            if constraints is None:
                rerank_result = self.reranker.rerank(effective_rerank_query, rerank_input)
            else:
                rerank_result = self.reranker.rerank(
                    effective_rerank_query,
                    rerank_input,
                    constraints=constraints,
                )
        except Exception as exc:
            if retrieval_trace is not None:
                retrieval_trace["failure_stage"] = "reranker_exception"
                retrieval_trace["reranker_error"] = f"{type(exc).__name__}: {exc}"
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
        if retrieval_trace is not None:
            retrieval_trace["reranker_status"] = rerank_result.status
            retrieval_trace["reranker_reason"] = rerank_result.reason
            retrieval_trace["selected_ld_ids"] = [
                candidate.ld_id for candidate in selected_candidates
            ]
            retrieval_trace["reranker_selected"] = [
                {
                    "candidate_id": selected_item.candidate_id,
                    "ld_id": selected_item.ld_id,
                    "confidence": selected_item.llm_confidence,
                    "reason": selected_item.reason,
                }
                for selected_item in selected
            ]
            if rerank_result.status == "MATCHED" and selected_candidates:
                retrieval_trace["failure_stage"] = None
            elif rerank_input:
                retrieval_trace["failure_stage"] = "reranker"
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
        started = perf_counter()
        trace_enabled = bool(getattr(self.settings, "match_trace_enabled", True))
        log_match_trace("web_enrichment_started", enabled=trace_enabled)
        try:
            result = self.competitor_lookup.lookup(query)
        except Exception as exc:  # enrichment must never break matching
            duration = perf_counter() - started
            record_web_enrichment("error", duration)
            debug = {
                "attempted": True,
                "accepted": False,
                "reason": f"lookup_error:{type(exc).__name__}:{exc}",
                "search_results": "",
                "pages": [],
                "duration_ms": round(duration * 1000, 3),
            }
            log_match_trace(
                "web_enrichment_failed",
                enabled=trace_enabled,
                duration_ms=debug["duration_ms"],
                error_type=type(exc).__name__,
            )
            return None, None, debug

        duration = perf_counter() - started
        debug = result.debug_payload()
        accepted = bool(debug.get("accepted", getattr(result, "accepted", False)))
        status = "accepted" if accepted else "rejected"
        record_web_enrichment(status, duration)
        debug["accepted"] = accepted
        debug["duration_ms"] = round(duration * 1000, 3)
        log_match_trace(
            "web_enrichment_completed",
            enabled=trace_enabled,
            duration_ms=debug["duration_ms"],
            status=status,
            reason=trim_text(debug.get("reason"), 120),
            page_count=len(debug.get("pages") or []),
        )
        return result, result.prompt_context(), debug

    def _log_enrichment_diagnostic(self, query: str, debug: dict) -> None:
        pages = debug.get("pages") or []
        reason = trim_text(debug.get("reason"), 220)
        accepted = bool(debug.get("accepted"))
        attempted = bool(debug.get("attempted"))
        duration_ms = debug.get("duration_ms")
        if not attempted:
            status = "SKIPPED"
            severity = "INFO"
        elif accepted:
            status = "ACCEPTED"
            severity = "INFO"
        elif str(reason).startswith("lookup_error") or "error" in str(reason).lower():
            status = "ERROR"
            severity = "ERROR"
        else:
            status = "REJECTED"
            severity = "WARNING"

        summary = f"WEB {status}"
        if reason:
            summary += f" — {reason}"
        if duration_ms is not None:
            summary += f" ({duration_ms:.0f} ms)"

        evidence_preview = []
        for page in pages[:2]:
            if not isinstance(page, dict):
                continue
            evidence_preview.append(
                {
                    "target": trim_text(page.get("target"), 300),
                    "text": trim_text(page.get("text"), 500),
                }
            )

        log_operational_event(
            "web_enrichment_diagnostic",
            summary,
            severity=severity,
            input_query=trim_text(query, 500),
            attempted=attempted,
            accepted=accepted,
            reason=reason,
            duration_ms=duration_ms,
            search_query=trim_text(debug.get("search_query"), 500),
            page_count=len(pages),
            search_results_preview=trim_text(debug.get("search_results"), 700),
            evidence_preview=evidence_preview,
        )

    def _log_attribute_diagnostic(
        self,
        query: str,
        *,
        pre_interpretation,
        interpretation,
        hard_constraints: dict,
    ) -> None:
        before = pre_interpretation.constraints.model_dump()
        after = interpretation.constraints.model_dump()
        diagnostic = build_attribute_diagnostic(before, after, hard_constraints)
        conflicts = diagnostic["hard_conflict_fields"]
        record_attribute_conflicts(conflicts)

        changed = diagnostic["changed_fields"]
        summary_parts = []
        if changed:
            summary_parts.append("changed: " + ", ".join(changed))
        else:
            summary_parts.append("attributes unchanged")
        if conflicts:
            summary_parts.append("HARD CONFLICT: " + ", ".join(conflicts))

        log_operational_event(
            "attribute_diagnostic",
            " | ".join(summary_parts),
            severity="WARNING" if conflicts else "INFO",
            input_query=trim_text(query, 500),
            changed_fields=changed,
            hard_conflict_fields=conflicts,
            hard_conflicts=diagnostic["hard_conflicts"],
            attributes_before_web=before,
            attributes_after_web=after,
            hard_constraints=hard_constraints,
        )

    def _enrichment_gate(self, query: str, interpretation) -> tuple[bool, str]:
        if self.competitor_lookup is None:
            return False, "enrichment_disabled"
        constraints = interpretation.constraints
        if constraints.catalog_scope == "out_of_scope":
            return False, "pre_enrichment_out_of_scope"
        if not has_product_identity(query):
            return False, "pre_enrichment_no_product_identity"
        # Rich explicit queries already carry enough technical facts; web adds
        # latency and can only introduce contradictions.
        if explicit_technical_signal_count(query) >= 4:
            return False, "pre_enrichment_enough_explicit_detail"
        # Ambiguous/uncertain model-only queries are exactly the cases web should
        # try to resolve. Keep rejecting non-searchable, non-ambiguous lines
        # (for example service-like requests) before external lookup.
        if (
            not interpretation.searchable
            and not constraints.ambiguous
            and constraints.catalog_scope != "uncertain"
        ):
            return False, "pre_enrichment_not_searchable"
        if constraints.ambiguous:
            return True, "pre_enrichment_ambiguous_identity_rescue"
        return True, "pre_enrichment_eligible"

    @staticmethod
    def _hard_constraints(query: str, pre_interpretation, enriched_interpretation=None) -> dict:
        """Build hard filters from explicit QUERY facts; LLM/web deductions stay soft."""
        pre = pre_interpretation.constraints
        enriched = enriched_interpretation.constraints if enriched_interpretation is not None else None

        explicit_product_type = canonical_product_type(query)
        if explicit_product_type == "other":
            if enriched is not None and enriched.product_type != "other":
                product_type = enriched.product_type
            else:
                product_type = pre.product_type
        else:
            product_type = explicit_product_type

        material = canonical_material(query)
        if material == "other":
            material = None
        bore = canonical_bore_type(query)
        control = canonical_control(query)
        designation = None
        if pre.valve_designation:
            query_compact = "".join(ch for ch in query.lower() if ch.isalnum())
            designation_compact = "".join(
                ch for ch in str(pre.valve_designation).lower() if ch.isalnum()
            )
            if designation_compact and designation_compact in query_compact:
                designation = pre.valve_designation

        text = query.lower().replace("ё", "е")
        valve_type = None
        if "подзем" in text:
            valve_type = "underground"
        elif "регулиру" in text:
            valve_type = "regulating"
        elif "криоген" in text:
            valve_type = "cryogenic"
        elif "газов" in text:
            valve_type = "gas"

        return {
            "product_type": product_type,
            "dn": explicit_dn_from_query(query),
            "pn_min_mpa": explicit_pn_mpa_from_query(query),
            "joining_type": explicit_joining_type_from_query(query),
            "thread_type": explicit_thread_type_from_query(query),
            "working_medium": explicit_working_medium_from_query(query),
            "valve_type": valve_type,
            "valve_designation": designation,
            "body_material": material,
            "body_material_grade": None,
            "bore_type": bore,
            "control": control,
            "catalog_scope": pre.catalog_scope,
            "ambiguous": pre.ambiguous,
            "comment": "hard constraints derived only from explicit QUERY facts",
        }

    def match_one_hybrid_with_rerank(self, query: str) -> MatchResult:
        query = self._normalize_query(query)
        if not query:
            return MatchResult(query=query, status="NOT_FOUND")
        if self.hybrid_retriever is None:
            raise ValueError("Hybrid retriever is not configured")

        if self.query_interpreter is not None:
            # Legacy decoder is an opt-in benchmark control only. Production
            # resolver-v2 runs without manufacturer-specific decoder logic.
            source_decode = (
                decode_competitor_query(query)
                if getattr(
                    self.settings,
                    "competitor_decoder_baseline_enabled",
                    False,
                )
                else None
            )
            competitor_resolution = None
            resolver_context = None
            if self.competitor_resolver is not None and source_decode is None:
                try:
                    competitor_resolution = self.competitor_resolver.resolve(query)
                    resolver_context = competitor_resolution.prompt_context()
                except Exception as exc:
                    log_match_trace(
                        "competitor_resolver_failed",
                        enabled=bool(getattr(self.settings, "match_trace_enabled", True)),
                        error_type=type(exc).__name__,
                    )

            try:
                pre_interpretation = self._interpret_query(
                    query,
                    competitor_context=resolver_context,
                )
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
            if competitor_resolution is not None and competitor_resolution.resolved:
                should_enrich = False
                gate_reason = "competitor_resolver_resolved"
            record_enrichment_gate(gate_reason)

            if should_enrich:
                _, competitor_context, lookup_debug = self._lookup_competitor(query)
                if (
                    competitor_context is not None
                    and self.competitor_resolver is not None
                    and not self.competitor_resolver.web_context_is_exact(
                        query,
                        lookup_debug,
                    )
                ):
                    competitor_context = None
                    if lookup_debug is not None:
                        lookup_debug["resolver_identity_gate"] = "rejected"
                        lookup_debug["resolver_identity_reason"] = (
                            "exact_product_anchor_not_found_in_fetched_pages"
                        )
                if competitor_context is not None:
                    try:
                        enriched_interpretation = self._interpret_query(
                            query,
                            competitor_context=competitor_context,
                        )
                        interpretation = enriched_interpretation
                        if (
                            self.competitor_resolver is not None
                            and source_decode is None
                        ):
                            learned = self.competitor_resolver.learn_from_web(
                                query,
                                enriched_interpretation.constraints.model_dump(),
                                lookup_debug,
                            )
                            if learned is not None and lookup_debug is not None:
                                lookup_debug["competitor_kb_write"] = learned.debug_payload()
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

            hard_constraints = self._hard_constraints(
                query,
                pre_interpretation,
                enriched_interpretation,
            )
            interpretation_payload = interpretation.model_dump()
            interpretation_payload["hard_constraints"] = hard_constraints
            if competitor_resolution is not None:
                interpretation_payload["competitor_resolution"] = (
                    competitor_resolution.debug_payload()
                )
            if source_decode is not None:
                interpretation_payload["source_decode"] = source_decode.debug_payload()
            if enriched_interpretation is not None:
                interpretation_payload["pre_enrichment_interpretation"] = (
                    pre_interpretation.model_dump()
                )
            if lookup_debug is not None:
                interpretation_payload["competitor_lookup"] = lookup_debug
                self._log_enrichment_diagnostic(query, lookup_debug)

            self._log_attribute_diagnostic(
                query,
                pre_interpretation=pre_interpretation,
                interpretation=interpretation,
                hard_constraints=hard_constraints,
            )

            if not interpretation.searchable:
                return MatchResult(
                    query=query,
                    status="NOT_FOUND",
                    reason=f"QUERY_REJECTED: {interpretation.reason}",
                    query_interpretation=interpretation_payload,
                )

            normalized_query = self._normalize_query(interpretation.normalized_query) or query

            # Search LD by canonical technical facts once resolver (or the
            # optional benchmark decoder) has resolved the source product.
            if (
                competitor_resolution is not None
                and competitor_resolution.resolved
                and normalized_query != query
            ):
                retrieval_query = normalized_query
                rendered_query = build_constraint_rendered_query(
                    interpretation.constraints
                )
                canonical_query = (
                    self._normalize_query(rendered_query)
                    if rendered_query
                    and self._normalize_query(rendered_query) != retrieval_query
                    else None
                )
                retrieval_strategy = "competitor_resolved_technical_plus_catalog"
            elif source_decode is not None and normalized_query != query:
                retrieval_query = normalized_query
                rendered_query = build_constraint_rendered_query(
                    interpretation.constraints
                )
                canonical_query = (
                    self._normalize_query(rendered_query)
                    if rendered_query
                    and self._normalize_query(rendered_query) != retrieval_query
                    else None
                )
                retrieval_strategy = "competitor_decoded_technical_plus_catalog"
            elif enriched_interpretation is not None and normalized_query != query:
                retrieval_query = normalized_query
                rendered_query = build_constraint_rendered_query(
                    enriched_interpretation.constraints
                )
                canonical_query = (
                    self._normalize_query(rendered_query)
                    if rendered_query
                    and self._normalize_query(rendered_query) != retrieval_query
                    else None
                )
                retrieval_strategy = "web_enriched_technical_plus_catalog"
            else:
                retrieval_query = query
                canonical_query = normalized_query if normalized_query != query else None
                retrieval_strategy = (
                    "original_plus_canonical"
                    if canonical_query is not None
                    else "original_only"
                )

            interpretation_payload["retrieval_trace"] = {
                "strategy": retrieval_strategy,
                "source_query": query,
                "retrieval_query": retrieval_query,
                "alternate_query": canonical_query,
                "pre_enrichment_normalized_query": self._normalize_query(
                    pre_interpretation.normalized_query
                ),
                "enriched_normalized_query": (
                    self._normalize_query(enriched_interpretation.normalized_query)
                    if enriched_interpretation is not None
                    else None
                ),
                "web_enrichment_applied": enriched_interpretation is not None,
            }

            detailed_trace = bool(
                getattr(self.settings, "match_trace_detailed_enabled", False)
            )
            if detailed_trace and hasattr(self.hybrid_retriever, "search_with_trace"):
                candidates, retriever_debug = self.hybrid_retriever.search_with_trace(
                    retrieval_query,
                    self.settings.hybrid_rerank_limit,
                    canonical_query=canonical_query,
                )
                interpretation_payload["retrieval_trace"]["retriever"] = retriever_debug
            else:
                candidates = self.hybrid_retriever.search(
                    retrieval_query,
                    self.settings.hybrid_rerank_limit,
                    canonical_query=canonical_query,
                )
            log_match_trace(
                "retrieval_completed",
                enabled=bool(getattr(self.settings, "match_trace_enabled", True)),
                strategy=retrieval_strategy,
                candidate_count=len(candidates),
                retrieval_query=trim_text(retrieval_query, 500),
                alternate_query=trim_text(canonical_query, 500),
            )
            return self.rerank_candidates(
                query,
                candidates,
                constraints=hard_constraints,
                query_interpretation=interpretation_payload,
                rerank_query=(
                    retrieval_query
                    if retrieval_strategy
                    in {
                        "web_enriched_technical_plus_catalog",
                        "competitor_decoded_technical_plus_catalog",
                        "competitor_resolved_technical_plus_catalog",
                    }
                    else query
                ),
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
