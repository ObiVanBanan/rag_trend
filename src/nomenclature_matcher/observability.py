"""Request context, structured tracing, and lightweight Prometheus metrics."""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from threading import RLock
from time import perf_counter
from typing import Any

REQUEST_ID_HEADER = "X-Request-ID"
PROMETHEUS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"
DEFAULT_HISTOGRAM_BUCKETS = (
    0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0,
    2.5, 5.0, 10.0, 20.0, 30.0, 60.0,
)

_request_id_context: ContextVar[str | None] = ContextVar(
    "rag_tender_request_id", default=None
)
_item_index_context: ContextVar[int | None] = ContextVar(
    "rag_tender_item_index", default=None
)
_logger = logging.getLogger("nomenclature_matcher.observability")

_DIAGNOSTIC_CONSTRAINT_FIELDS = (
    "product_type",
    "dn",
    "pn_min_mpa",
    "joining_type",
    "thread_type",
    "working_medium",
    "valve_type",
    "valve_designation",
    "body_material",
    "body_material_grade",
    "bore_type",
    "control",
)
_MATCH_REASON_CODES = (
    "MATCHED",
    "HARD_CONSTRAINT_FILTER",
    "QUERY_REJECTED",
    "QUERY_INTERPRET_FAILED",
    "RERANK_FAILED",
    "RERANK_NOT_FOUND",
    "UNSPECIFIED",
    "OTHER",
)


def get_request_id() -> str | None:
    return _request_id_context.get()


def set_request_id(request_id: str | None) -> object:
    return _request_id_context.set(request_id)


def reset_request_id(token: object) -> None:
    _request_id_context.reset(token)


def set_item_index(item_index: int | None) -> object:
    return _item_index_context.set(item_index)


def reset_item_index(token: object) -> None:
    _item_index_context.reset(token)


def resolve_request_id(header_value: str | None) -> str:
    from uuid import uuid4

    value = str(header_value or "").strip()
    return value[:128] if value else uuid4().hex


def _json_log(event: str, *, severity: str = "INFO", **payload: Any) -> None:
    request_id = payload.pop("request_id", None) or get_request_id()
    item_index = payload.pop("item_index", None)
    if item_index is None:
        item_index = _item_index_context.get()
    severity = str(severity or "INFO").upper()
    record = {"event": event, "severity": severity, "request_id": request_id}
    if item_index is not None:
        record["item_index"] = item_index
    record.update({key: value for key, value in payload.items() if value is not None})
    log_method = getattr(_logger, severity.lower(), _logger.info)
    log_method(json.dumps(record, ensure_ascii=False, separators=(",", ":")))


def log_operational_event(
    event: str,
    summary: str,
    *,
    severity: str = "INFO",
    **payload: Any,
) -> None:
    """Emit one human-oriented JSON event suitable for Loki/Grafana tables."""
    _json_log(
        event,
        severity=severity,
        summary=" ".join(str(summary or "").split())[:600],
        **payload,
    )


def trim_text(value: Any, limit: int = 600) -> str:
    return " ".join(str(value or "").split())[:limit]


def classify_match_reason(status: str, reason: str | None) -> str:
    if status == "MATCHED":
        return "MATCHED"
    text = str(reason or "").strip()
    upper = text.upper()
    if "HARD_CONSTRAINT_FILTER" in upper:
        return "HARD_CONSTRAINT_FILTER"
    if upper.startswith("QUERY_REJECTED"):
        return "QUERY_REJECTED"
    if upper.startswith("QUERY_INTERPRET_FAILED"):
        return "QUERY_INTERPRET_FAILED"
    if status in {"ERROR", "RERANK_FAILED"} or "RERANK_FAILED" in upper:
        return "RERANK_FAILED"
    if "NO EXACT MATCH" in upper or "NOT_FOUND" in upper:
        return "RERANK_NOT_FOUND"
    if not text:
        return "UNSPECIFIED"
    return "OTHER"


def build_attribute_diagnostic(
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    hard: dict[str, Any] | None,
) -> dict[str, Any]:
    before = before or {}
    after = after or {}
    hard = hard or {}
    changed_fields: list[str] = []
    hard_conflicts: dict[str, dict[str, Any]] = {}
    for field in _DIAGNOSTIC_CONSTRAINT_FIELDS:
        before_value = before.get(field)
        after_value = after.get(field)
        hard_value = hard.get(field)
        if before_value != after_value:
            changed_fields.append(field)
        if (
            hard_value not in (None, "")
            and after_value not in (None, "")
            and hard_value != after_value
        ):
            hard_conflicts[field] = {
                "hard": hard_value,
                "enriched": after_value,
            }
    return {
        "changed_fields": changed_fields,
        "hard_conflict_fields": sorted(hard_conflicts),
        "hard_conflicts": hard_conflicts,
    }


def log_match_trace(stage: str, *, enabled: bool = True, **payload: Any) -> None:
    if enabled:
        _json_log("match_trace", stage=stage, **payload)


def log_http_request_completed(
    *, request_id: str, method: str, path: str, status_code: int, duration_ms: float
) -> None:
    _json_log(
        "http_request_completed",
        request_id=request_id,
        method=method,
        path=path,
        status_code=status_code,
        duration_ms=round(duration_ms, 3),
    )


def _normalize_label_names(
    label_names: tuple[str, ...], labels: dict[str, Any]
) -> tuple[str, ...]:
    try:
        return tuple(str(labels[name]) for name in label_names)
    except KeyError as exc:
        raise KeyError(f"Missing metric label: {exc.args[0]}") from exc


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _format_labels(
    label_names: tuple[str, ...], label_values: tuple[str, ...]
) -> str:
    if not label_names:
        return ""
    parts = [
        f'{name}="{_escape_label_value(value)}"'
        for name, value in zip(label_names, label_values, strict=True)
    ]
    return "{" + ",".join(parts) + "}"


@dataclass(slots=True)
class CounterMetric:
    name: str
    help_text: str
    label_names: tuple[str, ...] = ()
    values: dict[tuple[str, ...], float] = field(default_factory=dict)
    lock: RLock = field(default_factory=RLock, repr=False)

    def inc(self, value: float = 1.0, **labels: Any) -> None:
        label_values = _normalize_label_names(self.label_names, labels)
        with self.lock:
            self.values[label_values] = self.values.get(label_values, 0.0) + value

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} counter"]
        for label_values, value in sorted(self.values.items()):
            lines.append(
                f"{self.name}{_format_labels(self.label_names, label_values)} {value:g}"
            )
        if not self.values and not self.label_names:
            lines.append(f"{self.name} 0")
        return lines


@dataclass(slots=True)
class GaugeMetric:
    name: str
    help_text: str
    label_names: tuple[str, ...] = ()
    values: dict[tuple[str, ...], float] = field(default_factory=dict)
    lock: RLock = field(default_factory=RLock, repr=False)

    def inc(self, value: float = 1.0, **labels: Any) -> None:
        label_values = _normalize_label_names(self.label_names, labels)
        with self.lock:
            self.values[label_values] = self.values.get(label_values, 0.0) + value

    def dec(self, value: float = 1.0, **labels: Any) -> None:
        self.inc(-value, **labels)

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} gauge"]
        for label_values, value in sorted(self.values.items()):
            lines.append(
                f"{self.name}{_format_labels(self.label_names, label_values)} {value:g}"
            )
        if not self.values and not self.label_names:
            lines.append(f"{self.name} 0")
        return lines


@dataclass(slots=True)
class HistogramMetric:
    name: str
    help_text: str
    label_names: tuple[str, ...] = ()
    buckets: tuple[float, ...] = DEFAULT_HISTOGRAM_BUCKETS
    values: dict[tuple[str, ...], dict[str, Any]] = field(default_factory=dict)
    lock: RLock = field(default_factory=RLock, repr=False)

    def observe(self, value: float, **labels: Any) -> None:
        label_values = _normalize_label_names(self.label_names, labels)
        with self.lock:
            series = self.values.setdefault(
                label_values,
                {"bucket_counts": [0 for _ in self.buckets], "count": 0, "sum": 0.0},
            )
            series["count"] += 1
            series["sum"] += value
            for index, bucket in enumerate(self.buckets):
                if value <= bucket:
                    series["bucket_counts"][index] += 1
                    break

    def render(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.help_text}", f"# TYPE {self.name} histogram"]
        for label_values, series in sorted(self.values.items()):
            cumulative = 0
            for bucket, bucket_count in zip(
                self.buckets, series["bucket_counts"], strict=True
            ):
                cumulative += bucket_count
                lines.append(
                    f"{self.name}_bucket"
                    f"{_format_labels(self.label_names + ('le',), label_values + (f'{bucket:g}',))}"
                    f" {cumulative:g}"
                )
            lines.append(
                f"{self.name}_bucket"
                f"{_format_labels(self.label_names + ('le',), label_values + ('+Inf',))}"
                f" {series['count']:g}"
            )
            lines.append(
                f"{self.name}_sum{_format_labels(self.label_names, label_values)} "
                f"{series['sum']:g}"
            )
            lines.append(
                f"{self.name}_count{_format_labels(self.label_names, label_values)} "
                f"{series['count']:g}"
            )
        return lines


HTTP_REQUESTS_TOTAL = CounterMetric(
    "rag_tender_http_requests_total",
    "Total RAG Tender HTTP requests.",
    ("method", "path", "status_code"),
)
HTTP_REQUEST_DURATION = HistogramMetric(
    "rag_tender_http_request_duration_seconds",
    "RAG Tender HTTP request duration in seconds.",
    ("method", "path"),
)
HTTP_REQUESTS_IN_FLIGHT = GaugeMetric(
    "rag_tender_http_requests_in_flight",
    "RAG Tender in-flight HTTP requests.",
)
MATCH_ITEMS_TOTAL = CounterMetric(
    "rag_tender_match_items_total",
    "Matched batch items by result status.",
    ("status",),
)
MATCH_ITEM_DURATION = HistogramMetric(
    "rag_tender_match_item_duration_seconds",
    "End-to-end matching duration for one product.",
    ("status",),
)
BATCH_SIZE = HistogramMetric(
    "rag_tender_batch_size",
    "Number of products in one /v1/match request.",
    buckets=(1.0, 2.0, 3.0, 5.0, 10.0, 25.0, 50.0, 100.0),
)
API_ERRORS_TOTAL = CounterMetric(
    "rag_tender_api_errors_total",
    "RAG Tender API errors by bounded machine-readable code.",
    ("code",),
)
EMBEDDING_REQUESTS_TOTAL = CounterMetric(
    "rag_tender_embedding_requests_total", "Embedding API calls."
)
EMBEDDING_ERRORS_TOTAL = CounterMetric(
    "rag_tender_embedding_errors_total",
    "Embedding API errors by exception type.",
    ("error_type",),
)
EMBEDDING_DURATION = HistogramMetric(
    "rag_tender_embedding_duration_seconds", "Embedding API duration."
)
QUERY_INTERPRETER_REQUESTS_TOTAL = CounterMetric(
    "rag_tender_query_interpreter_requests_total", "Query interpreter calls."
)
QUERY_INTERPRETER_ERRORS_TOTAL = CounterMetric(
    "rag_tender_query_interpreter_errors_total",
    "Query interpreter errors by exception type.",
    ("error_type",),
)
QUERY_INTERPRETER_DURATION = HistogramMetric(
    "rag_tender_query_interpreter_duration_seconds", "Query interpreter duration."
)
RERANK_REQUESTS_TOTAL = CounterMetric(
    "rag_tender_rerank_requests_total", "DeepSeek reranker calls."
)
RERANK_ERRORS_TOTAL = CounterMetric(
    "rag_tender_rerank_errors_total",
    "DeepSeek reranker errors by exception type.",
    ("error_type",),
)
RERANK_DURATION = HistogramMetric(
    "rag_tender_rerank_duration_seconds", "DeepSeek reranker duration."
)
QDRANT_REQUESTS_TOTAL = CounterMetric(
    "rag_tender_qdrant_requests_total", "Qdrant search calls."
)
QDRANT_ERRORS_TOTAL = CounterMetric(
    "rag_tender_qdrant_errors_total",
    "Qdrant search errors by exception type.",
    ("error_type",),
)
QDRANT_DURATION = HistogramMetric(
    "rag_tender_qdrant_duration_seconds", "Qdrant search duration."
)
HISTORY_WRITES_TOTAL = CounterMetric(
    "rag_tender_history_writes_total",
    "PostgreSQL history writes by status.",
    ("status",),
)
HISTORY_DURATION = HistogramMetric(
    "rag_tender_history_duration_seconds", "PostgreSQL history write duration."
)
WEB_ENRICHMENT_TOTAL = CounterMetric(
    "rag_tender_web_enrichment_total",
    "Web enrichment attempts by bounded outcome.",
    ("status",),
)
WEB_ENRICHMENT_DURATION = HistogramMetric(
    "rag_tender_web_enrichment_duration_seconds",
    "Web enrichment duration in seconds.",
    ("status",),
)
ENRICHMENT_GATE_TOTAL = CounterMetric(
    "rag_tender_enrichment_gate_total",
    "Enrichment gate decisions by bounded reason.",
    ("reason",),
)
ATTRIBUTE_CONFLICTS_TOTAL = CounterMetric(
    "rag_tender_attribute_conflicts_total",
    "Conflicts between hard constraints and enriched attributes.",
    ("field",),
)
MATCH_DECISIONS_TOTAL = CounterMetric(
    "rag_tender_match_decisions_total",
    "Final item decisions by status and bounded reason.",
    ("status", "reason"),
)

_METRICS = (
    HTTP_REQUESTS_TOTAL,
    HTTP_REQUEST_DURATION,
    HTTP_REQUESTS_IN_FLIGHT,
    MATCH_ITEMS_TOTAL,
    MATCH_ITEM_DURATION,
    BATCH_SIZE,
    API_ERRORS_TOTAL,
    EMBEDDING_REQUESTS_TOTAL,
    EMBEDDING_ERRORS_TOTAL,
    EMBEDDING_DURATION,
    QUERY_INTERPRETER_REQUESTS_TOTAL,
    QUERY_INTERPRETER_ERRORS_TOTAL,
    QUERY_INTERPRETER_DURATION,
    RERANK_REQUESTS_TOTAL,
    RERANK_ERRORS_TOTAL,
    RERANK_DURATION,
    QDRANT_REQUESTS_TOTAL,
    QDRANT_ERRORS_TOTAL,
    QDRANT_DURATION,
    HISTORY_WRITES_TOTAL,
    HISTORY_DURATION,
    WEB_ENRICHMENT_TOTAL,
    WEB_ENRICHMENT_DURATION,
    ENRICHMENT_GATE_TOTAL,
    ATTRIBUTE_CONFLICTS_TOTAL,
    MATCH_DECISIONS_TOTAL,
)


def record_http_request(
    *, method: str, path: str, status_code: int, duration_seconds: float
) -> None:
    HTTP_REQUESTS_TOTAL.inc(method=method, path=path, status_code=str(status_code))
    HTTP_REQUEST_DURATION.observe(duration_seconds, method=method, path=path)


def inc_in_flight() -> None:
    HTTP_REQUESTS_IN_FLIGHT.inc()


def dec_in_flight() -> None:
    HTTP_REQUESTS_IN_FLIGHT.dec()


def record_match_item(status: str, duration_seconds: float) -> None:
    MATCH_ITEMS_TOTAL.inc(status=status)
    MATCH_ITEM_DURATION.observe(duration_seconds, status=status)


def record_batch_size(size: int) -> None:
    BATCH_SIZE.observe(float(size))


def record_api_error(code: str) -> None:
    API_ERRORS_TOTAL.inc(code=code)


def record_history_write(status: str, duration_seconds: float) -> None:
    HISTORY_WRITES_TOTAL.inc(status=status)
    HISTORY_DURATION.observe(duration_seconds)


def record_web_enrichment(status: str, duration_seconds: float) -> None:
    bounded = status if status in {"accepted", "rejected", "error"} else "error"
    WEB_ENRICHMENT_TOTAL.inc(status=bounded)
    WEB_ENRICHMENT_DURATION.observe(duration_seconds, status=bounded)


def record_enrichment_gate(reason: str) -> None:
    mapping = {
        "pre_enrichment_eligible": "eligible",
        "pre_enrichment_out_of_scope": "out_of_scope",
        "pre_enrichment_ambiguous": "ambiguous",
        "pre_enrichment_not_searchable": "not_searchable",
        "pre_enrichment_no_product_identity": "no_identity",
        "pre_enrichment_enough_explicit_detail": "enough_detail",
        "enrichment_disabled": "disabled",
    }
    ENRICHMENT_GATE_TOTAL.inc(reason=mapping.get(reason, "other"))


def record_attribute_conflicts(fields: list[str]) -> None:
    allowed = set(_DIAGNOSTIC_CONSTRAINT_FIELDS)
    for field in fields:
        ATTRIBUTE_CONFLICTS_TOTAL.inc(field=field if field in allowed else "other")


def record_match_decision(status: str, reason: str | None) -> str:
    code = classify_match_reason(status, reason)
    if code not in _MATCH_REASON_CODES:
        code = "OTHER"
    MATCH_DECISIONS_TOTAL.inc(status=status, reason=code)
    return code


def render_metrics() -> str:
    lines: list[str] = []
    for metric in _METRICS:
        lines.extend(metric.render())
    return "\n".join(lines) + "\n"


class _ObservedProxy:
    def __init__(self, target: Any, settings: Any):
        self._target = target
        self._settings = settings

    def __getattr__(self, name: str) -> Any:
        return getattr(self._target, name)

    @property
    def trace_enabled(self) -> bool:
        return bool(getattr(self._settings, "match_trace_enabled", True))


class ObservedEmbedder(_ObservedProxy):
    def _call(self, operation: str, fn, *args):
        started = perf_counter()
        log_match_trace(f"embedding_{operation}_started", enabled=self.trace_enabled)
        try:
            result = fn(*args)
        except Exception as exc:
            duration = perf_counter() - started
            EMBEDDING_REQUESTS_TOTAL.inc()
            EMBEDDING_ERRORS_TOTAL.inc(error_type=type(exc).__name__)
            EMBEDDING_DURATION.observe(duration)
            log_match_trace(
                f"embedding_{operation}_failed",
                enabled=self.trace_enabled,
                duration_ms=round(duration * 1000, 3),
                error_type=type(exc).__name__,
            )
            raise
        duration = perf_counter() - started
        EMBEDDING_REQUESTS_TOTAL.inc()
        EMBEDDING_DURATION.observe(duration)
        log_match_trace(
            f"embedding_{operation}_completed",
            enabled=self.trace_enabled,
            duration_ms=round(duration * 1000, 3),
        )
        return result

    def embed_query(self, text: str) -> list[float]:
        return self._call("query", self._target.embed_query, text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._call("documents", self._target.embed_documents, texts)


class ObservedQdrantStore(_ObservedProxy):
    def search(self, vector, limit):
        started = perf_counter()
        log_match_trace(
            "qdrant_started", enabled=self.trace_enabled, candidate_limit=limit
        )
        try:
            result = self._target.search(vector, limit)
        except Exception as exc:
            duration = perf_counter() - started
            QDRANT_REQUESTS_TOTAL.inc()
            QDRANT_ERRORS_TOTAL.inc(error_type=type(exc).__name__)
            QDRANT_DURATION.observe(duration)
            log_match_trace(
                "qdrant_failed",
                enabled=self.trace_enabled,
                duration_ms=round(duration * 1000, 3),
                error_type=type(exc).__name__,
            )
            raise
        duration = perf_counter() - started
        QDRANT_REQUESTS_TOTAL.inc()
        QDRANT_DURATION.observe(duration)
        log_match_trace(
            "qdrant_completed",
            enabled=self.trace_enabled,
            duration_ms=round(duration * 1000, 3),
            candidate_count=len(result),
        )
        return result


class ObservedQueryInterpreter(_ObservedProxy):
    def interpret(self, query: str, competitor_context: dict | None = None):
        started = perf_counter()
        log_match_trace("interpreter_started", enabled=self.trace_enabled)
        try:
            result = self._target.interpret(
                query, competitor_context=competitor_context
            )
        except Exception as exc:
            duration = perf_counter() - started
            QUERY_INTERPRETER_REQUESTS_TOTAL.inc()
            QUERY_INTERPRETER_ERRORS_TOTAL.inc(error_type=type(exc).__name__)
            QUERY_INTERPRETER_DURATION.observe(duration)
            log_match_trace(
                "interpreter_failed",
                enabled=self.trace_enabled,
                duration_ms=round(duration * 1000, 3),
                error_type=type(exc).__name__,
            )
            raise
        duration = perf_counter() - started
        QUERY_INTERPRETER_REQUESTS_TOTAL.inc()
        QUERY_INTERPRETER_DURATION.observe(duration)
        log_match_trace(
            "interpreter_completed",
            enabled=self.trace_enabled,
            duration_ms=round(duration * 1000, 3),
            searchable=bool(getattr(result, "searchable", False)),
        )
        return result


class ObservedReranker(_ObservedProxy):
    def rerank(self, query: str, candidates: list[Any], **kwargs):
        started = perf_counter()
        log_match_trace(
            "rerank_started",
            enabled=self.trace_enabled,
            candidate_count=len(candidates),
        )
        try:
            result = self._target.rerank(query, candidates, **kwargs)
        except Exception as exc:
            duration = perf_counter() - started
            RERANK_REQUESTS_TOTAL.inc()
            RERANK_ERRORS_TOTAL.inc(error_type=type(exc).__name__)
            RERANK_DURATION.observe(duration)
            log_match_trace(
                "rerank_failed",
                enabled=self.trace_enabled,
                duration_ms=round(duration * 1000, 3),
                error_type=type(exc).__name__,
            )
            raise
        duration = perf_counter() - started
        RERANK_REQUESTS_TOTAL.inc()
        RERANK_DURATION.observe(duration)
        log_match_trace(
            "rerank_completed",
            enabled=self.trace_enabled,
            duration_ms=round(duration * 1000, 3),
            status=getattr(result, "status", None),
            selected_count=len(getattr(result, "selected", []) or []),
        )
        return result
