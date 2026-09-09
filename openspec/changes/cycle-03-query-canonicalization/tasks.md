## 1. Deterministic Retrieval Canonicalization

- [x] 1.1 Add a small pure query-canonicalization module that returns a normalized source query and at most one distinct canonical retrieval query, covering only the documented confusable designation tokens, compact DN/PN forms, allowed joining/actuation abbreviations, count-only noise, and safely anchored item clauses; verify focused unit tests cover each supported transform and an unchanged-input no-op.
- [x] 1.2 Ensure canonicalization preserves explicit product family, designation, DN, PN, material, joining, and control text and does not infer catalog ids or engineering constraints; verify focused tests include mixed-script designation, compact notation, and long tender prose with a quantity.
- [x] 1.3 Ensure unsupported product wording, blank input, ambiguous/no-anchor prose, and already canonical text produce no catalog-domain expansion; verify focused negative and no-op tests.

## 2. Additive Hybrid Retrieval

- [x] 2.1 Extend hybrid retrieval to accept an optional distinct canonical query and retrieve it in addition to the original query without changing the existing one-query caller behavior; verify mocked dense and BM25 tests assert both calls only when an alternate exists.
- [x] 2.2 Merge original and canonical result sets by LD id per retrieval modality, retain the strongest available rank/score per modality, and apply the existing RRF contribution once per modality; verify duplicate-product, canonical-only-product, and original-only regression tests.
- [x] 2.3 Preserve existing candidate payload fields, configured limits, and failure behavior when alternate retrieval is absent or fails; verify focused tests assert no duplicate candidates and usable original-query candidates after an alternate-query failure.

## 3. Matcher Integration and Regression Coverage

- [x] 3.1 Wire hybrid matching to pass the bounded canonical retrieval query while keeping the original normalized tender text for deduplication, reranker input, `MatchResult.query`, and result serialization; verify matcher tests with a recording retriever and mocked reranker.
- [x] 3.2 Add focused regression tests for compact/mixed-script technical tender forms and for catalog-external negative queries, asserting canonicalization is additive and no false catalog-domain terms are injected.
- [x] 3.3 Run `python -m pytest -q` and resolve any failures while keeping all product-code changes confined to this experiment’s query-time path.
