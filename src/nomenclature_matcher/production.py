from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .bm25_store import BM25Store
from .documents import load_products_from_csv
from .embeddings import OpenAIEmbedder
from .hybrid_retriever import HybridRetriever
from .matcher import NomenclatureMatcher
from .observability import (
    ObservedEmbedder,
    ObservedQdrantStore,
    ObservedQueryInterpreter,
    ObservedReranker,
    build_attribute_diagnostic,
    log_match_trace,
    log_operational_event,
    record_match_decision,
    record_match_item,
    reset_item_index,
    set_item_index,
    trim_text,
)
from .qdrant_store import QdrantStore
from .query_interpreter import DeepSeekQueryInterpreter
from .reranker import DeepSeekReranker
from .settings import Settings

logger = logging.getLogger(__name__)


class PostgresRequestHistory:
    def __init__(self, settings: Settings):
        self.open_timeout = settings.db_connect_timeout_seconds
        self.pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            timeout=settings.db_connect_timeout_seconds,
            kwargs={"autocommit": True},
            open=False,
        )

    def open(self) -> None:
        self.pool.open(wait=True, timeout=self.open_timeout)
        with self.pool.connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rag_match_requests (
                    request_id UUID PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL,
                    completed_at TIMESTAMPTZ NOT NULL,
                    status TEXT NOT NULL,
                    http_status INTEGER NOT NULL,
                    product_count INTEGER NOT NULL,
                    matched_count INTEGER NOT NULL,
                    latency_ms DOUBLE PRECISION NOT NULL,
                    request_json JSONB NOT NULL,
                    response_json JSONB NOT NULL,
                    error TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_rag_match_requests_created_at
                ON rag_match_requests (created_at DESC)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_rag_match_requests_status
                ON rag_match_requests (status)
                """
            )
            conn.execute(
                """
                CREATE OR REPLACE VIEW rag_match_items AS
                SELECT
                    r.request_id,
                    r.created_at,
                    r.completed_at,
                    r.status AS request_status,
                    r.latency_ms AS request_latency_ms,
                    item.ordinality::INTEGER AS item_index,
                    item.value->>'input' AS input,
                    item.value->>'status' AS item_status,
                    item.value->>'matched_name' AS matched_name,
                    item.value->>'matched_article' AS matched_article,
                    NULLIF(item.value->>'score', '')::DOUBLE PRECISION AS score,
                    NULLIF(item.value->>'confidence', '')::DOUBLE PRECISION AS confidence,
                    item.value->>'error_code' AS error_code
                FROM rag_match_requests AS r
                CROSS JOIN LATERAL
                    jsonb_array_elements(
                        COALESCE(r.response_json->'results', '[]'::jsonb)
                    ) WITH ORDINALITY AS item(value, ordinality)
                """
            )

    def record(
        self,
        *,
        request_id: str,
        created_at: datetime,
        completed_at: datetime,
        status: str,
        http_status: int,
        product_count: int,
        matched_count: int,
        latency_ms: float,
        request_payload: dict[str, Any],
        response_payload: dict[str, Any],
        error: str | None = None,
    ) -> None:
        with self.pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO rag_match_requests (
                    request_id, created_at, completed_at, status, http_status,
                    product_count, matched_count, latency_ms,
                    request_json, response_json, error
                )
                VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s
                )
                """,
                (
                    request_id,
                    created_at,
                    completed_at,
                    status,
                    http_status,
                    product_count,
                    matched_count,
                    latency_ms,
                    Jsonb(request_payload),
                    Jsonb(response_payload),
                    error,
                ),
            )

    def ping(self) -> bool:
        try:
            with self.pool.connection() as conn:
                row = conn.execute("SELECT 1").fetchone()
            return bool(row and row[0] == 1)
        except Exception:
            return False

    def close(self) -> None:
        self.pool.close()


@dataclass
class ProductionRuntime:
    settings: Settings
    matcher: NomenclatureMatcher
    qdrant_store: Any
    history: PostgresRequestHistory

    def ready(self) -> bool:
        if not self.history.ping():
            return False
        try:
            return bool(
                self.qdrant_store.client.collection_exists(
                    self.settings.qdrant_collection_alias
                )
            )
        except Exception:
            return False

    def close(self) -> None:
        lookup = getattr(self.matcher, "competitor_lookup", None)
        close_lookup = getattr(lookup, "close", None)
        if callable(close_lookup):
            close_lookup()
        self.history.close()


def _validate_runtime_settings(settings: Settings) -> None:
    missing = []
    if not settings.openai_api_key:
        missing.append("OPENAI_API_KEY")
    if not settings.deepseek_api_key:
        missing.append("DEEPSEEK_API_KEY")
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")

    csv_path = Path(settings.product_csv_path)
    if not csv_path.is_file():
        raise RuntimeError(f"Product catalog CSV not found: {csv_path}")


def build_production_runtime(settings: Settings | None = None) -> ProductionRuntime:
    settings = settings or Settings()
    logging.basicConfig(
        level=getattr(logging, str(settings.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _validate_runtime_settings(settings)

    products = load_products_from_csv(settings.product_csv_path)

    base_embedder = OpenAIEmbedder(settings)
    embedder = ObservedEmbedder(base_embedder, settings)

    base_qdrant_store = QdrantStore(settings)
    qdrant_store = ObservedQdrantStore(base_qdrant_store, settings)

    if not qdrant_store.client.collection_exists(settings.qdrant_collection_alias):
        raise RuntimeError(
            "Qdrant collection "
            f"{settings.qdrant_collection_alias!r} is not available. "
            "Restore the champion snapshot or rebuild the index before starting the API."
        )

    query_interpreter = ObservedQueryInterpreter(
        DeepSeekQueryInterpreter(settings), settings
    )
    reranker = ObservedReranker(DeepSeekReranker(settings), settings)

    matcher = NomenclatureMatcher(
        embedder,
        qdrant_store,
        settings,
        reranker=reranker,
        hybrid_retriever=HybridRetriever(
            embedder,
            qdrant_store,
            BM25Store(products),
            settings,
        ),
        query_interpreter=query_interpreter,
    )

    history = PostgresRequestHistory(settings)
    try:
        history.open()
    except Exception:
        lookup = getattr(matcher, "competitor_lookup", None)
        close_lookup = getattr(lookup, "close", None)
        if callable(close_lookup):
            close_lookup()
        raise

    logger.info(
        "Production runtime ready: products=%s qdrant_collection=%s",
        len(products),
        settings.qdrant_collection_alias,
    )
    return ProductionRuntime(
        settings=settings,
        matcher=matcher,
        qdrant_store=qdrant_store,
        history=history,
    )


def match_products(matcher: NomenclatureMatcher, products: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    trace_enabled = bool(getattr(getattr(matcher, "settings", None), "match_trace_enabled", True))

    for index, product in enumerate(products, 1):
        token = set_item_index(index)
        started = perf_counter()
        log_match_trace("item_started", enabled=trace_enabled)
        try:
            match = None
            exception_reason = None
            try:
                match = matcher.match_one_hybrid_with_rerank(product)
            except Exception as exc:
                logger.exception("Product matching failed for one batch item")
                exception_reason = f"{type(exc).__name__}: {exc}"
                item = {
                    "input": product,
                    "status": "ERROR",
                    "matched_name": None,
                    "matched_article": None,
                    "score": None,
                    "confidence": None,
                    "error_code": type(exc).__name__,
                }
            else:
                if match.status == "MATCHED" and match.ld_product is not None:
                    confidence = (
                        match.selected[0].llm_confidence
                        if match.selected
                        else None
                    )
                    item = {
                        "input": product,
                        "status": "MATCHED",
                        "matched_name": match.ld_product.name,
                        "matched_article": match.ld_product.article,
                        "score": match.score,
                        "confidence": confidence,
                        "error_code": None,
                    }
                elif match.status == "RERANK_FAILED":
                    item = {
                        "input": product,
                        "status": "ERROR",
                        "matched_name": None,
                        "matched_article": None,
                        "score": None,
                        "confidence": None,
                        "error_code": "RERANK_FAILED",
                    }
                else:
                    item = {
                        "input": product,
                        "status": "NOT_FOUND",
                        "matched_name": None,
                        "matched_article": None,
                        "score": None,
                        "confidence": None,
                        "error_code": None,
                    }

            duration = perf_counter() - started
            results.append(item)
            record_match_item(item["status"], duration)

            reason = getattr(match, "reason", None) if match is not None else exception_reason
            reason_code = record_match_decision(item["status"], reason)
            interpretation = (
                getattr(match, "query_interpretation", None) or {}
                if match is not None
                else {}
            )
            lookup = interpretation.get("competitor_lookup") or {}
            after = interpretation.get("constraints") or {}
            before = (
                (interpretation.get("pre_enrichment_interpretation") or {}).get("constraints")
                or after
            )
            hard = interpretation.get("hard_constraints") or {}
            attribute_diagnostic = build_attribute_diagnostic(before, after, hard)
            conflict_fields = attribute_diagnostic["hard_conflict_fields"]

            if item["status"] == "MATCHED":
                summary = (
                    f"MATCHED → {item['matched_article'] or item['matched_name'] or 'unknown'} "
                    f"(confidence={item['confidence']}, {duration * 1000:.0f} ms)"
                )
                severity = "INFO"
            elif item["status"] == "NOT_FOUND":
                summary = f"NOT_FOUND — {reason_code} ({duration * 1000:.0f} ms)"
                severity = "WARNING"
            else:
                summary = (
                    f"ERROR — {item['error_code'] or reason_code} "
                    f"({duration * 1000:.0f} ms)"
                )
                severity = "ERROR"
            if conflict_fields:
                summary += " | HARD CONFLICT: " + ", ".join(conflict_fields)
                severity = "WARNING" if severity == "INFO" else severity

            log_operational_event(
                "item_diagnostic",
                summary,
                severity=severity,
                input_query=trim_text(product, 500),
                status=item["status"],
                reason_code=reason_code,
                reason=trim_text(reason, 600),
                duration_ms=round(duration * 1000, 3),
                matched_name=item["matched_name"],
                matched_article=item["matched_article"],
                score=item["score"],
                confidence=item["confidence"],
                error_code=item["error_code"],
                candidate_count=len(getattr(match, "candidates", []) or []) if match is not None else 0,
                web_attempted=lookup.get("attempted"),
                web_accepted=lookup.get("accepted"),
                web_reason=trim_text(lookup.get("reason"), 220),
                web_duration_ms=lookup.get("duration_ms"),
                hard_conflict_fields=conflict_fields,
            )

            log_match_trace(
                "item_completed",
                enabled=trace_enabled,
                status=item["status"],
                duration_ms=round(duration * 1000, 3),
                error_type=item["error_code"],
            )
        finally:
            reset_item_index(token)

    return results
