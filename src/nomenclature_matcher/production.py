from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .bm25_store import BM25Store
from .documents import load_products_from_csv
from .embeddings import OpenAIEmbedder
from .hybrid_retriever import HybridRetriever
from .matcher import NomenclatureMatcher
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
                    request_id TEXT PRIMARY KEY,
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
            conn.execute("DROP VIEW IF EXISTS rag_match_items")
            conn.execute(
                """
                ALTER TABLE rag_match_requests
                ALTER COLUMN request_id TYPE TEXT
                USING request_id::text
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
    qdrant_store: QdrantStore
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


def _warm_web_search(matcher: NomenclatureMatcher) -> bool:
    lookup = getattr(matcher, "competitor_lookup", None)
    warmup = getattr(lookup, "warmup", None)
    if not callable(warmup):
        return True
    try:
        warmup()
    except Exception:
        logger.exception(
            "Web search MCP warmup failed; starting API with web enrichment temporarily bypassed"
        )
        return False
    logger.info("Web search MCP warmup completed")
    return True


def build_production_runtime(settings: Settings | None = None) -> ProductionRuntime:
    settings = settings or Settings()
    logging.basicConfig(
        level=getattr(logging, str(settings.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    _validate_runtime_settings(settings)

    products = load_products_from_csv(settings.product_csv_path)
    embedder = OpenAIEmbedder(settings)
    qdrant_store = QdrantStore(settings)

    if not qdrant_store.client.collection_exists(settings.qdrant_collection_alias):
        raise RuntimeError(
            "Qdrant collection "
            f"{settings.qdrant_collection_alias!r} is not available. "
            "Restore the champion snapshot or rebuild the index before starting the API."
        )

    matcher = NomenclatureMatcher(
        embedder,
        qdrant_store,
        settings,
        reranker=DeepSeekReranker(settings),
        hybrid_retriever=HybridRetriever(
            embedder,
            qdrant_store,
            BM25Store(products),
            settings,
        ),
        query_interpreter=DeepSeekQueryInterpreter(settings),
    )

    _warm_web_search(matcher)

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
    for product in products:
        try:
            match = matcher.match_one_hybrid_with_rerank(product)
        except Exception as exc:
            logger.exception("Product matching failed for one batch item")
            results.append(
                {
                    "input": product,
                    "status": "ERROR",
                    "matched_name": None,
                    "matched_article": None,
                    "score": None,
                    "confidence": None,
                    "error_code": type(exc).__name__,
                }
            )
            continue

        if match.status == "MATCHED" and match.ld_product is not None:
            confidence = (
                match.selected[0].llm_confidence
                if match.selected
                else None
            )
            results.append(
                {
                    "input": product,
                    "status": "MATCHED",
                    "matched_name": match.ld_product.name,
                    "matched_article": match.ld_product.article,
                    "score": match.score,
                    "confidence": confidence,
                    "error_code": None,
                }
            )
        elif match.status == "RERANK_FAILED":
            results.append(
                {
                    "input": product,
                    "status": "ERROR",
                    "matched_name": None,
                    "matched_article": None,
                    "score": None,
                    "confidence": None,
                    "error_code": "RERANK_FAILED",
                }
            )
        else:
            results.append(
                {
                    "input": product,
                    "status": "NOT_FOUND",
                    "matched_name": None,
                    "matched_article": None,
                    "score": None,
                    "confidence": None,
                    "error_code": None,
                }
            )
    return results
