## 1. Conservative connection-code expansion

- [x] 1.1 Extend the retrieval-query canonicalization boundary with the documented case-insensitive, standalone `WW` allowlist entry that adds Russian welded-connection vocabulary while preserving all source terms; verify focused canonicalization tests assert the exact alternate query and unchanged source query.
- [x] 1.2 Enforce strict token boundaries and no-op behavior for embedded `WW`, unknown connection-like codes, and unsupported/no-product queries; verify focused negative tests assert that no welded vocabulary is injected from those tokens.

## 2. Additive retrieval regression coverage

- [x] 2.1 Verify the existing hybrid path queries both original and `WW`-expanded text only when a distinct alternate is emitted, deduplicates candidates by LD id, and retains original-query candidates after alternate retrieval failure; add or update mocked hybrid-retriever tests.
- [x] 2.2 Verify matcher integration keeps the original tender string as reranker input, `MatchResult.query`, and output text for a `WW` query; add or update a recording-retriever/mocked-reranker test.

## 3. Verification

- [x] 3.1 Run focused query-canonicalization, hybrid-retriever, and matcher tests, then run `python -m pytest -q`; resolve product-code test failures without expanding the approved notation scope.
