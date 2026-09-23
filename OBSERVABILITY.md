# RAG Tender observability

The production matcher exposes three operator-facing layers:

1. **Prometheus** — rates, outcomes, latency and bounded failure reasons.
2. **Loki** — one-line human-oriented diagnostics plus detailed stage traces.
3. **PostgreSQL** — durable request history and the `rag_match_items` view.

## Fastest incident workflow

Open **RAG Platform → RAG Tender — Operations**.

Start with:

- **Recent Problems** — warnings/errors only.
- **Recent Item Decisions** — one line per input item.
- **Web Enrichment Diagnostics** — whether web search ran, why it failed and how long it took.
- **Hard Constraint Conflicts** — fields where enriched attributes disagree with frozen hard constraints.
- **Request Timeline** — paste the response `request_id` to drill into every stage.

Typical operator events:

```text
MATCHED → A-123 (confidence=0.95, 2300 ms)
NOT_FOUND — HARD_CONSTRAINT_FILTER (1800 ms)
WEB ERROR — lookup_error:... (43800 ms)
changed: dn, body_material | HARD CONFLICT: dn
REQUEST OK: 2 matched, 1 not found, 0 errors of 3 items (8400 ms)
```

Every JSON event contains `request_id`; item-level events also contain
`item_index`.

## Prometheus metrics

Core API:

- `rag_tender_http_requests_total`
- `rag_tender_http_request_duration_seconds`
- `rag_tender_match_items_total{status}`
- `rag_tender_match_item_duration_seconds{status}`

Pipeline:

- `rag_tender_query_interpreter_duration_seconds`
- `rag_tender_embedding_duration_seconds`
- `rag_tender_qdrant_duration_seconds`
- `rag_tender_rerank_duration_seconds`

Diagnostics:

- `rag_tender_match_decisions_total{status,reason}`
- `rag_tender_web_enrichment_total{status}`
- `rag_tender_web_enrichment_duration_seconds{status}`
- `rag_tender_enrichment_gate_total{reason}`
- `rag_tender_attribute_conflicts_total{field}`

Only bounded labels are used in Prometheus. Query text, model/article values and
`request_id` remain in Loki/PostgreSQL.

## Loki events

### `item_diagnostic`

One final operator summary for every product:

- input query
- final status
- bounded reason code
- matched name/article
- score/confidence
- candidate count
- web enrichment outcome
- hard-conflict fields
- total item latency

### `web_enrichment_diagnostic`

Shows:

- attempted / accepted
- failure reason
- duration
- actual search query
- result preview
- up to two page evidence previews

### `attribute_diagnostic`

Shows side-by-side:

- `attributes_before_web`
- `attributes_after_web`
- `hard_constraints`
- changed fields
- hard-vs-enriched conflicts

This event is specifically intended to expose cases such as an initial
`dn=15` interpretation followed by web evidence for `dn=20`.

### `match_trace`

Low-level stage timeline:

```text
request_started
item_started
interpreter_started/completed
web_enrichment_started/completed/failed
embedding_query_started/completed
qdrant_started/completed
rerank_started/completed
item_completed
persist_started/completed
request_completed
```

## PostgreSQL

`rag_match_requests` remains the durable request/response audit table.

The API also creates the view:

```text
rag_match_items
```

which expands batch responses to one row per item with request ID, input,
status, matched article/name, score, confidence and request latency. This is
intended for Grafana business tables and ad-hoc SQL.

## Shared monitoring network

The monitoring stack and RAG Tender must share:

```bash
docker network inspect rag-observability-net >/dev/null 2>&1 || \
  docker network create rag-observability-net
```

The current shared monitoring stack scrapes:

```text
rag-observability-api:8000/metrics
rag-observability-qdrant:6333/metrics
```

Alloy discovers both `rag-tender-api` and `rag-observability-api` container
names and normalizes them to the Loki label:

```text
service=rag-tender-api
```

## Proxy configuration

Do not commit proxy credentials. Put the proxy URL only in the server `.env`:

```env
OUTBOUND_PROXY_URL=socks5://user:password@host:port
```

The compose override passes it to the API process via `HTTP_PROXY`,
`HTTPS_PROXY` and `ALL_PROXY`.

## Smoke test

```bash
curl http://127.0.0.1:8002/health/ready
curl http://127.0.0.1:8002/metrics | grep rag_tender_

curl -i -X POST http://127.0.0.1:8002/v1/match \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: tender-smoke-001' \
  -d '{"products":["Кран шаровый VT.217.N.05"]}'
```

Then open **RAG Tender — Operations** and filter the Request Timeline by
`tender-smoke-001`.
