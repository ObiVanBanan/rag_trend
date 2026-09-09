## Why

The current champion still returns a deterministic-requirement violation for a terse welded ball-valve request using the common `WW` end-connection notation. The accepted Cycle 3 canonicalizer repairs DN/PN and several Russian abbreviations but leaves `WW` unchanged, even though the catalog contains verified welded DN100 PN25 ball-valve candidates; a bounded notation expansion is therefore a high-information, low-safety-risk recall experiment.

## What Changes

- Add deterministic recognition of unambiguous end-connection abbreviations, beginning with `WW` (weld-weld), and emit their catalog-native Russian connection wording in the additive canonical retrieval query.
- Define an explicit, conservative allowlist for supported connection codes and their token boundaries; unrecognized, ambiguous, or embedded letter sequences must remain unchanged.
- Preserve the source tender query, original-query retrieval, existing candidate fusion, reranker input, and final-selection semantics.
- Add focused unit and integration tests for supported codes, malformed/ambiguous codes, and original-query fallback behavior.

## Capabilities

### New Capabilities

- `connection-notation-canonicalization`: Produces a bounded additive retrieval representation for safe technical end-connection notation without inferring product identities or requirements.

### Modified Capabilities

- None.

## Impact

- Affected package code: `query_canonicalization.py`, with possible focused matcher/hybrid-retriever wiring only if required to preserve existing additive behavior.
- Affected tests: query canonicalization and hybrid/matcher regression tests using mocks.
- No DeepSeek prompt, catalog content, embedding model, Qdrant payload, BM25 corpus, collection alias, or full index rebuild is required.
