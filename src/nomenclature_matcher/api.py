from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from time import perf_counter
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, field_validator

from .observability import (
    PROMETHEUS_CONTENT_TYPE,
    dec_in_flight,
    get_request_id,
    inc_in_flight,
    log_http_request_completed,
    log_match_trace,
    record_api_error,
    record_batch_size,
    record_history_write,
    record_http_request,
    render_metrics,
    reset_request_id,
    resolve_request_id,
    set_request_id,
)
from .production import ProductionRuntime, build_production_runtime, match_products

logger = logging.getLogger(__name__)


class MatchRequest(BaseModel):
    products: list[str] = Field(min_length=1)

    @field_validator("products")
    @classmethod
    def validate_products(cls, value: list[str]) -> list[str]:
        for index, product in enumerate(value):
            if not isinstance(product, str) or not product.strip():
                raise ValueError(f"products[{index}] must be a non-empty string")
        return value


class ProductMatch(BaseModel):
    input: str
    status: Literal["MATCHED", "NOT_FOUND", "ERROR"]
    matched_name: str | None = None
    matched_article: str | None = None
    score: float | None = None
    confidence: float | None = None
    error_code: str | None = None


class MatchResponse(BaseModel):
    request_id: str
    results: list[ProductMatch]
    latency_ms: float


RuntimeFactory = Callable[[], ProductionRuntime]


def create_app(runtime_factory: RuntimeFactory | None = None) -> FastAPI:
    runtime_factory = runtime_factory or build_production_runtime

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = runtime_factory()
        app.state.runtime = runtime
        try:
            yield
        finally:
            runtime.close()

    app = FastAPI(
        title="RAG Tender Matcher API",
        version="1.0.0",
        description=(
            "Production API around the validated rag-tender champion pipeline. "
            "Each product is matched independently."
        ),
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        request_id = resolve_request_id(request.headers.get("X-Request-ID"))
        request.state.request_id = request_id
        token = set_request_id(request_id)
        inc_in_flight()
        started = perf_counter()
        status_code = 500
        response = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            duration = perf_counter() - started
            if response is not None:
                response.headers["X-Request-ID"] = request_id
            dec_in_flight()
            record_http_request(
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_seconds=duration,
            )
            log_http_request_completed(
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=duration * 1000,
            )
            reset_request_id(token)

    @app.get("/health/live")
    def health_live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready")
    def health_ready(request: Request):
        runtime: ProductionRuntime = request.app.state.runtime
        if not runtime.ready():
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return {"status": "ok"}

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return Response(content=render_metrics(), media_type=PROMETHEUS_CONTENT_TYPE)

    @app.post("/v1/match", response_model=MatchResponse)
    def match(payload: MatchRequest, request: Request):
        runtime: ProductionRuntime = request.app.state.runtime
        if len(payload.products) > runtime.settings.api_max_batch_size:
            raise HTTPException(
                status_code=413,
                detail=(
                    "Batch is too large. "
                    f"Maximum size is {runtime.settings.api_max_batch_size}."
                ),
            )

        request_id = get_request_id() or resolve_request_id(None)
        created_at = datetime.now(timezone.utc)
        started = perf_counter()
        request_payload = payload.model_dump(mode="json")
        trace_enabled = bool(getattr(runtime.settings, "match_trace_enabled", True))
        record_batch_size(len(payload.products))
        log_match_trace(
            "request_started",
            enabled=trace_enabled,
            batch_size=len(payload.products),
        )

        try:
            raw_results = match_products(runtime.matcher, payload.products)
            latency_ms = round((perf_counter() - started) * 1000, 3)
            response = MatchResponse(
                request_id=request_id,
                results=[ProductMatch.model_validate(item) for item in raw_results],
                latency_ms=latency_ms,
            )
            response_payload = response.model_dump(mode="json")
            completed_at = datetime.now(timezone.utc)
            matched_count = sum(
                item.status == "MATCHED" for item in response.results
            )
            history_status = (
                "PARTIAL_ERROR"
                if any(item.status == "ERROR" for item in response.results)
                else "OK"
            )
            _record_history_safely(
                runtime,
                request_id=request_id,
                created_at=created_at,
                completed_at=completed_at,
                status=history_status,
                http_status=200,
                product_count=len(payload.products),
                matched_count=matched_count,
                latency_ms=latency_ms,
                request_payload=request_payload,
                response_payload=response_payload,
                trace_enabled=trace_enabled,
            )
            log_match_trace(
                "request_completed",
                enabled=trace_enabled,
                duration_ms=latency_ms,
                batch_size=len(payload.products),
                matched_count=matched_count,
                not_found_count=sum(
                    item.status == "NOT_FOUND" for item in response.results
                ),
                error_count=sum(item.status == "ERROR" for item in response.results),
            )
            return response
        except Exception as exc:
            latency_ms = round((perf_counter() - started) * 1000, 3)
            completed_at = datetime.now(timezone.utc)
            logger.exception("Unhandled production API error")
            record_api_error("INTERNAL_ERROR")
            response_payload = {
                "request_id": request_id,
                "error": "internal_error",
            }
            _record_history_safely(
                runtime,
                request_id=request_id,
                created_at=created_at,
                completed_at=completed_at,
                status="ERROR",
                http_status=500,
                product_count=len(payload.products),
                matched_count=0,
                latency_ms=latency_ms,
                request_payload=request_payload,
                response_payload=response_payload,
                error=f"{type(exc).__name__}: {exc}",
                trace_enabled=trace_enabled,
            )
            log_match_trace(
                "request_failed",
                enabled=trace_enabled,
                duration_ms=latency_ms,
                error_type=type(exc).__name__,
            )
            return JSONResponse(status_code=500, content=response_payload)

    return app


def _record_history_safely(
    runtime: ProductionRuntime,
    *,
    request_id: str,
    created_at: datetime,
    completed_at: datetime,
    status: str,
    http_status: int,
    product_count: int,
    matched_count: int,
    latency_ms: float,
    request_payload: dict,
    response_payload: dict,
    error: str | None = None,
    trace_enabled: bool = True,
) -> None:
    started = perf_counter()
    log_match_trace("persist_started", enabled=trace_enabled)
    try:
        runtime.history.record(
            request_id=request_id,
            created_at=created_at,
            completed_at=completed_at,
            status=status,
            http_status=http_status,
            product_count=product_count,
            matched_count=matched_count,
            latency_ms=latency_ms,
            request_payload=request_payload,
            response_payload=response_payload,
            error=error,
        )
    except Exception as exc:
        duration = perf_counter() - started
        record_history_write("ERROR", duration)
        record_api_error("HISTORY_WRITE_FAILED")
        log_match_trace(
            "persist_failed",
            enabled=trace_enabled,
            duration_ms=round(duration * 1000, 3),
            error_type=type(exc).__name__,
        )
        logger.exception("Failed to persist request/response history")
    else:
        duration = perf_counter() - started
        record_history_write("OK", duration)
        log_match_trace(
            "persist_completed",
            enabled=trace_enabled,
            duration_ms=round(duration * 1000, 3),
        )


app = create_app()
