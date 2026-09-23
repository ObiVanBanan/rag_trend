# RAG Tender Matcher

Production-oriented tender product matching service for LD nomenclature.
The matcher combines dense Qdrant retrieval, BM25/RRF retrieval and LLM
reranking. A request can return `MATCHED`, `NOT_FOUND` or an explicit error.

## Quick start

Prerequisites: Docker, Docker Compose, Git LFS and API keys from `.env.example`.

```bash
git lfs pull
cp .env.example .env
docker compose up -d --build
curl http://127.0.0.1:8000/health/ready
```

The local catalog export `ld_products_full_nomenclature.csv` is intentionally
not stored in Git. Provide it through the path configured by
`PRODUCT_CSV_PATH` when building an index or running the matcher.

## API

- `GET /health/ready` — readiness check.
- `POST /v1/match` — match one or more tender product strings.
- `GET /metrics` — Prometheus metrics.

The API accepts `X-Request-ID` and returns the same ID in the response header
and JSON payload. PostgreSQL stores request history; Qdrant stores the vector
index.

## Development

```bash
uv sync
uv run pytest -q
docker compose config
```

Do not commit `.env`, database exports, local logs, generated `egg-info`, or
temporary evaluation output. Large source data and Qdrant snapshots use Git
LFS where applicable.

## Layout

```text
src/                    application and matching pipeline
scripts/                indexing, evaluation and batch utilities
tests/                  unit and API tests
qdrant/                 Qdrant snapshot assets
docker-compose.yml      local production-like stack
```