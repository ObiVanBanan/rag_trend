## Context

See `proposal.md` for the experiment motivation. `NomenclatureMatcher._normalize_query` currently collapses whitespace only, and `HybridRetriever.search` uses that same raw text once for dense and BM25 retrieval. The reranker already receives the original query and catalog search text. Existing retrieval provenance represents only dense/BM25 ranks, so query variants must not inflate RRF merely by duplicating the same product across variants.

## Goals / Non-Goals

**Goals:**

- Improve candidate recall for catalog-vocabulary gaps caused by presentation noise and compact technical tender notation.
- Preserve original query semantics and all existing final-selection behavior.
- Make alternate retrieval text inspectable and deterministic so it can be unit-tested and removed cleanly if the experiment is rejected.
- Avoid a full index rebuild by changing query-time behavior only.

**Non-Goals:**

- No subject-matter claim that legacy competitor designations are equivalent to a particular LD family.
- No LLM-generated rewrite, automatic constraint extraction, candidate filtering, threshold calibration, or reranker prompt change.
- No catalog document, embedding, Qdrant payload, BM25 corpus, or collection-alias change.
- No interpretation of pipe OD, temperature range, flange subtype, or other currently unmodeled requirements.

## Decisions

### Add one bounded canonical retrieval representation

Introduce a small pure query-canonicalization boundary returning the normalized original and either zero or one canonical retrieval query. It will apply a narrowly documented table/rule set: confusable-character repair only for alphanumeric designation tokens, standard spacing for compact DN/PN forms, supported joining/actuation abbreviations, removal of count-only suffixes, and extraction of a supported item clause from surrounding prose when it is unambiguous.

This is deliberately additive: the raw normalized query is always retrieved. A free-form LLM rewrite was considered but rejected because it can hallucinate pressure, material, or execution requirements and makes failures hard to reproduce. A broad alias-to-product mapping was rejected for this cycle because the taxonomy says those equivalences are not yet validated.

### Fuse per modality after cross-representation deduplication

Run dense and BM25 retrieval for the original query and, only when distinct, the canonical query. Merge by LD id inside each modality and retain the best rank/score observed for that modality; then use the existing RRF calculation once per modality. This gives a candidate surfaced by either representation a fair entry while preventing duplicate rank contributions for the same dense/BM25 channel.

Using four independent RRF terms was considered and rejected: it would overweight a product merely because a spelling normalization returned it twice. Replacing the current RRF or changing candidate limits is out of scope, so a measured outcome isolates query representation.

### Keep original query at the reranker boundary and expose diagnostics

The reranker continues to receive the original normalized tender string, not the alternate representation. Candidate objects keep the current retrieval evidence contract; the canonicalization result should be available in an optional, non-production diagnostic path suitable for tests and local debugging. If alternate-query retrieval raises an operational error, the original-query retrieval result remains usable rather than failing the whole match.

This avoids silently changing the business decision text and makes the experiment fail-open. It also avoids repeating cycles 1 and 2, which modified compatibility handling closer to final selection and regressed public safety.

## Risks / Trade-offs

- [A normalization loses a meaningful token] → Limit transformations to a documented allowlist, preserve source text, and use regression tests for DN/PN/material/control/designation retention.
- [An extracted clause omits a necessary constraint] → Use clause extraction only when a supported product phrase anchors it; otherwise emit no alternate query and retain original retrieval.
- [Additional retrieval calls increase latency] → At most one alternate query per unique non-blank request, reuse existing configured limits, and skip it when identical to the original.
- [Alternative candidates increase false-match opportunity] → Preserve original reranker input and candidate cap, add negative-query regression tests, and rely on outer evaluation for promotion.
- [RRF evidence becomes ambiguous] → Deduplicate per modality before one existing RRF contribution, with tests for duplicated candidates and rank selection.

## Migration Plan

1. Add the pure canonicalization function and unit tests for supported transformations and safe no-op cases.
2. Integrate additive retrieval in the hybrid retriever while preserving its current API for callers without an alternate query.
3. Wire the matcher to generate and pass at most one alternate retrieval representation while preserving result query text and caching semantics.
4. Run focused tests and the full pytest suite. The outer harness owns public/blind evaluation and promotion.
5. Roll back by disabling/removing the canonical-query argument; no index or persisted-data rollback is needed.
