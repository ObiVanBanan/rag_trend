# ADR-001: Defer runtime web search; use catalog/KB-first resolution

- **Status:** Accepted for resolver v2
- **Date:** 2026-10-07
- **Related:** Issue #12, PR #14
- **Scope:** competitor source understanding / CompetitorResolver

## Context

The original resolver design used synchronous web search as a fallback when an
exact competitor product was not already present in the knowledge base.

The resolver-only frozen-100 experiment (seed 53, decoder disabled, fresh KB)
showed:

- top1: **8%**
- acceptable in initial retrieval: **22%**
- acceptable in rerank candidates: **22%**
- RETRIEVAL_MISS: **78**
- web accepted: **3**
- web rejected after attempt: **54**
- web skipped: **43**
- catalog-resolved requests: **6**

The important split was by source-understanding method.

### Catalog/schema path

TEMPER had a generic catalog schema for exact product articles.

Observed result:

- 6 of 8 TEMPER cases were resolved through the catalog schema;
- TEMPER top1 improved from 0/8 in the old unresolved path to 4/8;
- TEMPER initial retrieval coverage improved from 2/8 to 6/8;
- exact product records were written to the v2 KB;
- no family-level record was written as an exact product.

This path was deterministic and did not depend on live search availability.

### Runtime web path

The synchronous web fallback did not demonstrate useful incremental value in
this experiment:

- only 3/100 requests were accepted;
- 54/100 attempted searches were rejected/failed;
- accepted web contexts produced `web_context_no_technical_query`;
- accepted web results did not become a new resolver technical-query strategy;
- many failed attempts incurred multi-second latency, commonly around the
  configured timeout;
- DDG/search-provider failures made benchmark runtime and results noisy.

For MARSHAL and ALSO, the absence of catalog ingestion was the dominant
problem. Live web search did not compensate for that missing knowledge.

## Decision

**Do not use live web search in the primary synchronous matching path for
resolver v2.**

The primary source-understanding flow becomes:

```text
input product
    ↓
exact ProductRecord in KB?
    ↓ miss
manufacturer catalog / catalog schema / ingested source
    ↓ miss
unresolved / UNKNOWN
```

Web search is moved to a separate **knowledge acquisition** role:

```text
unknown manufacturer or missing catalog coverage
    ↓
source discovery
    ↓
find official manufacturer page / PDF / catalog
    ↓
inspect and validate source
    ↓
build or update catalog ingestion/schema
    ↓
persist knowledge
    ↓
future runtime requests use KB/catalog without live web search
```

Runtime matching should therefore be deterministic with respect to external
search availability.

The existing web implementation does not need to be deleted immediately. It
may remain behind an explicit experimental/offline path, but it should be
disabled by default for production matching and benchmark runs.

## Why

The purpose of web search was to improve cold-start coverage for unknown
products.

In the measured resolver-v2 run it instead produced:

1. very low accepted coverage;
2. high failure/timeout rate;
3. no observed accepted case that generated the resolver technical-query path;
4. significant latency and benchmark noise.

By contrast, catalog/schema resolution produced a clear deterministic quality
gain for TEMPER.

The current bottleneck is therefore **catalog knowledge coverage**, not search
orchestration.

## Consequences

### Positive

- deterministic benchmark results;
- lower request latency;
- less dependency on DDG/search-provider availability;
- fewer moving parts in source understanding;
- easier provenance and exact-product validation;
- effort is focused on the part that already demonstrated value: catalog
  ingestion.

### Negative

- a completely unknown manufacturer cannot be learned synchronously from one
  request;
- new manufacturers require a knowledge-acquisition/ingestion step;
- catalog coverage becomes an explicit operational responsibility.

These tradeoffs are accepted for v2.

## What we do next

Priority order:

1. generic MARSHAL catalog ingestion / nomenclature schema;
2. generic ALSO catalog ingestion / nomenclature schema;
3. TEMPER MAX coverage;
4. BROEN/other manufacturer catalog coverage where useful;
5. rerun resolver-only frozen-100 with web disabled and a fresh KB.

Do not hand-enter exact DEV benchmark answers. Add reusable manufacturer
catalog knowledge or generic ingestion rules.

## When to reconsider web search

Re-open this decision only if at least one of these becomes true:

- catalog/KB coverage plateaus because important products have no accessible
  structured manufacturer source;
- real production traffic contains a material number of previously unknown
  manufacturers that cannot be handled by scheduled ingestion;
- a reliable search provider replaces the current unstable runtime search;
- an isolated A/B experiment shows that web fallback provides a material gain
  in exact identity/attribute coverage or retrieval recall that justifies its
  latency and failure rate.

Any future experiment should compare:

```text
catalog/KB only
vs
catalog/KB + web fallback
```

and measure separately:

- exact identity coverage;
- attribute coverage/accuracy;
- acceptable_in_initial;
- acceptable_in_rerank;
- top1;
- latency;
- search failure rate.

Web search should only return to the synchronous production path if that
experiment shows clear incremental value.

## Alternatives considered

### Keep web fallback on every unresolved request

Rejected for resolver v2 because the measured path was slow, unstable and did
not compensate for missing MARSHAL/ALSO catalog knowledge.

### Delete web support completely

Not chosen. Web may still be useful for offline source discovery and for a
future cold-start experiment with a better provider.

### Use an autonomous/ReAct research loop

Deferred. Adding more search orchestration would increase complexity before
the simpler catalog-first approach has been exhausted.

## Summary

The experiment did not show that web research is useless in general.

It showed that **live web search is currently the wrong place in the
architecture**.

For resolver v2:

> Runtime resolves from exact KB and ingested manufacturer catalogs. Web is a
> deferred knowledge-acquisition tool, not a per-request dependency.
