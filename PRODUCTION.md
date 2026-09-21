# Production API

This branch wraps the validated rag-tender champion in a production-oriented HTTP service without changing the matching algorithm.

## Runtime

- FastAPI: HTTP API and OpenAPI docs.
- Qdrant: champion dense index.
- PostgreSQL: full request/response history.
- Existing matcher: hybrid dense + BM25 + RRF + DeepSeek query interpretation/reranking.
- Optional DuckDuckGo MCP enrichment remains enabled exactly as configured by the champion.
- Grafana/Prometheus are intentionally deferred to backlog issue #4.

The API runs with one Uvicorn worker by default because the product catalog and BM25 index are kept in memory. Increasing workers duplicates that memory.

## First start

The Qdrant snapshot is stored through Git LFS. Make sure it is materialized before Docker starts:

```powershell
git lfs install
git lfs pull
Copy-Item .env.example .env
```

Set at least these values in `.env`:

```text
OPENAI_API_KEY=...
DEEPSEEK_API_KEY=...
POSTGRES_PASSWORD=change-me
```

Then start the stack:

```powershell
docker compose up -d --build
docker compose ps
```

The `qdrant-init` one-shot container restores `qdrant/steel_products_active.snapshot` only when the collection does not already exist. It fails with a clear message if the snapshot is still a Git LFS pointer.

## Health and docs

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
```

Swagger UI:

```text
http://localhost:8000/docs
```

## Match API

Request:

```json
{
  "products": [
    "Кран шаровой муфтовый Ду25",
    "Задвижка неизвестного типа XYZ"
  ]
}
```

PowerShell:

```powershell
$body = @{
  products = @(
    "Кран шаровой муфтовый Ду25",
    "Задвижка неизвестного типа XYZ"
  )
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8000/v1/match `
  -ContentType "application/json" `
  -Body $body
```

Response contract:

```json
{
  "request_id": "uuid",
  "results": [
    {
      "input": "Кран шаровой муфтовый Ду25",
      "status": "MATCHED",
      "matched_name": "...",
      "matched_article": "...",
      "score": 0.031,
      "confidence": 0.96,
      "error_code": null
    }
  ],
  "latency_ms": 1234.5
}
```

For `NOT_FOUND`, `matched_name`, `matched_article`, `score`, and `confidence` are null. One product failure does not fail the rest of the batch; that item gets `status=ERROR`.

The maximum batch size is controlled by `API_MAX_BATCH_SIZE`.

## Request history

Every completed API call is written to PostgreSQL table `rag_match_requests`, including:

- request ID and timestamps;
- full request JSON;
- full response JSON;
- total latency;
- product count and match count;
- HTTP/status information;
- request-level error, when present.

Inspect recent calls:

```powershell
docker compose exec postgres psql -U rag_tender -d rag_tender -c "SELECT created_at, request_id, status, product_count, matched_count, latency_ms FROM rag_match_requests ORDER BY created_at DESC LIMIT 20;"
```

## Tests

```powershell
uv sync --extra test
uv run python -m pytest -q
```

Quick production-contract test:

```powershell
uv run python -m pytest -q tests/test_production_api.py
```
