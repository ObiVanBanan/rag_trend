## Why

The current hybrid matcher sends only whitespace-normalized tender text to both retrievers. Public hard-gate failures include compact/mixed-script designation text embedded in a long tender sentence and terse technical notation that does not use the catalog's vocabulary; this can bury compatible ordinary products under generic or specialized candidates. The prior two cycles tested candidate compatibility interventions and regressed public safety, so a retrieval-input experiment is now the highest-value distinct mechanism to test without rebuilding the index.

## What Changes

- Add deterministic, domain-preserving canonicalization for retrieval queries while retaining the original tender text for LLM reranking and returned results.
- Normalize safe lexical variants that do not add engineering requirements: Unicode-confusable Latin/Cyrillic characters inside designation-like tokens, compact DN/PN notation, and known joining/actuation abbreviations.
- Extract the item-bearing clause from multi-item tender prose only when an unambiguous, supported product phrase and its adjacent technical attributes are present; use the canonical clause as an additional retrieval representation rather than replacing the original query.
- Issue dense and BM25 retrieval for the original normalized query plus a bounded canonical retrieval query, then merge candidates by LD id using existing source/rank evidence and RRF behavior.
- Keep negative, blank, and unsupported product queries from gaining catalog-domain expansion; retain present behavior if canonicalization produces no meaningful alternate query.

## Capabilities

### New Capabilities

- `retrieval-query-canonicalization`: Builds bounded, deterministic catalog-native retrieval representations from tender nomenclature while preserving original-query selection semantics.

### Modified Capabilities

- None.

## Impact

- Affected package code: a new query canonicalization module plus `matcher.py` and/or `hybrid_retriever.py` integration.
- Affected behavior: hybrid candidate generation may receive an additional canonical query; DeepSeek reranking continues to receive the original tender string and the merged candidate set.
- Affected tests: focused canonicalization, hybrid merge/deduplication, original-query preservation, and negative-query regression tests.
- External systems: no model or Qdrant schema change and no full index rebuild.
