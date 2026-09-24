from types import SimpleNamespace

from fastapi.testclient import TestClient

from nomenclature_matcher.api import create_app
from nomenclature_matcher.models import MatchResult, SearchCandidate, SelectedMatch
from nomenclature_matcher.production import PostgresRequestHistory, match_products


class FakeMatcher:
    settings = SimpleNamespace(match_trace_enabled=False)

    def match_one_hybrid_with_rerank(self, query):
        if query == "boom":
            raise RuntimeError("provider unavailable")
        if query == "missing":
            return MatchResult(query=query, status="NOT_FOUND")
        candidate = SearchCandidate(
            ld_id=7,
            name="LD Product",
            article="LD-007",
            score=0.031,
        )
        selected = SelectedMatch(
            candidate_id=1,
            article="LD-007",
            name="LD Product",
            llm_confidence=0.97,
            reason="best",
            ld_id=7,
        )
        return MatchResult(
            query=query,
            status="MATCHED",
            score=0.031,
            ld_product=candidate,
            selected=[selected],
        )


class FakeHistory:
    def __init__(self):
        self.records = []

    def record(self, **kwargs):
        self.records.append(kwargs)

    def ping(self):
        return True

    def close(self):
        pass


class FakeRuntime:
    def __init__(self):
        self.settings = SimpleNamespace(
            api_max_batch_size=3,
            qdrant_collection_alias="steel_products_active",
            match_trace_enabled=False,
        )
        self.matcher = FakeMatcher()
        self.history = FakeHistory()
        self.closed = False

    def ready(self):
        return True

    def close(self):
        self.closed = True


def test_match_products_keeps_batch_items_independent():
    results = match_products(FakeMatcher(), ["ok", "missing", "boom"])

    assert [item["status"] for item in results] == [
        "MATCHED",
        "NOT_FOUND",
        "ERROR",
    ]
    assert results[0]["matched_article"] == "LD-007"
    assert results[0]["confidence"] == 0.97
    assert results[1]["matched_name"] is None
    assert results[2]["error_code"] == "RuntimeError"


def test_api_contract_history_request_id_and_metrics():
    runtime = FakeRuntime()
    app = create_app(runtime_factory=lambda: runtime)

    with TestClient(app) as client:
        health = client.get("/health/ready")
        assert health.status_code == 200

        response = client.post(
            "/v1/match",
            headers={"X-Request-ID": "e2e-request-123"},
            json={"products": ["ok", "missing", "boom"]},
        )
        metrics = client.get("/metrics")

    assert response.status_code == 200
    payload = response.json()
    assert payload["request_id"] == "e2e-request-123"
    assert response.headers["X-Request-ID"] == "e2e-request-123"
    assert [item["status"] for item in payload["results"]] == [
        "MATCHED",
        "NOT_FOUND",
        "ERROR",
    ]
    assert payload["results"][0]["matched_name"] == "LD Product"
    assert payload["results"][0]["matched_article"] == "LD-007"
    assert payload["results"][0]["score"] == 0.031
    assert payload["results"][0]["confidence"] == 0.97
    assert runtime.history.records[0]["request_id"] == "e2e-request-123"
    assert runtime.history.records[0]["status"] == "PARTIAL_ERROR"
    assert runtime.history.records[0]["product_count"] == 3
    assert runtime.history.records[0]["matched_count"] == 1
    assert metrics.status_code == 200
    assert "rag_tender_http_requests_total" in metrics.text
    assert "rag_tender_match_items_total" in metrics.text
    assert runtime.closed is True


def test_api_rejects_oversized_batch():
    runtime = FakeRuntime()
    app = create_app(runtime_factory=lambda: runtime)

    with TestClient(app) as client:
        response = client.post(
            "/v1/match",
            json={"products": ["a", "b", "c", "d"]},
        )

    assert response.status_code == 413



def test_history_schema_accepts_external_request_ids():
    class FakeConnection:
        def __init__(self):
            self.statements = []

        def execute(self, statement, *args):
            self.statements.append(str(statement))
            return self

    class ConnectionContext:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self.connection

        def __exit__(self, exc_type, exc, tb):
            return False

    class FakePool:
        def __init__(self):
            self.connection_object = FakeConnection()
            self.opened = False

        def open(self, wait=True, timeout=None):
            self.opened = True

        def connection(self):
            return ConnectionContext(self.connection_object)

    history = object.__new__(PostgresRequestHistory)
    history.open_timeout = 1
    history.pool = FakePool()
    history.open()

    sql = "\n".join(history.pool.connection_object.statements)
    assert "request_id TEXT PRIMARY KEY" in sql
    assert "ALTER COLUMN request_id TYPE TEXT" in sql
    assert "USING request_id::text" in sql
