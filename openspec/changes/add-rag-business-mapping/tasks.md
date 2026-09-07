## 1. Contract Alignment

- [x] 1.1 Review the current hybrid retriever, reranker, and LLM selection behavior against `specs/rag-business-mapping/spec.md` and verify the comparison is captured as concrete code changes or no-op confirmations in the implementation notes.
- [x] 1.2 Ensure batch matching accepts `list[str]`, preserves input order and cardinality, handles blanks as `NOT_FOUND`, and verify with focused tests in `tests/test_matcher.py`.
- [x] 1.3 Ensure duplicate non-blank queries are retrieved once after whitespace normalization while preserving repeated output entries, and verify embedder/store call counts in unit tests.

## 2. Hybrid Retrieval And Selection

- [x] 2.1 Ensure hybrid retrieval combines dense and BM25 candidate evidence with retained ranks/scores/fusion values, and verify with focused tests in `tests/test_hybrid_retriever.py`.
- [x] 2.2 Ensure reranked candidates are passed to LLM selection in the expected order and verify with mocked reranker/LLM tests.
- [x] 2.3 Ensure LLM selection can return `MATCHED` with a candidate or `NOT_FOUND` with no selected product, and verify confidence/reason handling with unit tests.

## 3. Result Payload

- [x] 3.1 Add or confirm a stable serialization boundary for mapping results that emits `query`, `status`, production `ld_product`, retained candidates, and development details, and verify with unit tests that matched and `NOT_FOUND` payloads are JSON-compatible.
- [x] 3.2 Ensure production selected LD product payloads include only available `name` and `article`, and verify with a payload serialization test.
- [x] 3.3 Ensure development/debug payloads include available candidate id, LD id, article, name, DN, PN, joining type, price, URL, properties, retrieval scores, ranks, LLM confidence, and reason, and verify with a matcher payload extraction test.

## 4. Index And Retrieval Inputs

- [x] 4.1 Confirm LD search text includes business fields from `tz.md` and excludes service metadata, and verify existing or new tests in `tests/test_documents.py`.
- [x] 4.2 Confirm per-request matching embeds only query text and searches the existing Qdrant/BM25 indexes without rebuilding LD documents, and verify with mocked embedder/store/indexer boundaries.
- [x] 4.3 Keep CSV indexing operational for local MVP validation and document the PostgreSQL loader boundary or add a loader only if database configuration is available, then verify `scripts/build_index.py --csv ld_products_full_nomenclature.csv` remains the supported local command.

## 5. Business-Facing Entry Point

- [x] 5.1 Add or update a thin batch entrypoint that accepts JSON/list nomenclature input and returns structured JSON mapping results, and verify it with a local command using at least the examples from `tz.md`.
- [x] 5.2 Keep single-query CLI behavior compatible with the existing README examples, and verify hybrid-rerank search still prints candidate and LLM selection details for manual debugging.
- [x] 5.3 Document the MVP usage path, required settings, hybrid retrieval limits, reranker/LLM configuration, and quality evaluation workflow, and verify README or adjacent docs point to the correct commands.

## 6. Quality Evaluation

- [x] 6.1 Confirm the reviewed evaluation dataset format includes query id, query text, expected status, acceptable LD identifiers or articles, and review status, and verify loader tests cover both `MATCHED` and `NOT_FOUND` examples.
- [x] 6.2 Implement or update evaluation metrics for dense Recall@K, BM25 Recall@K, hybrid Recall@K, final LLM selection accuracy, `wrong_not_found_rate`, `false_match_rate`, wrong product selection rate, and error breakdown, and verify metric unit tests cover each category.
- [x] 6.3 Ensure evaluation output includes per-query candidates, selected/rejected LLM decisions, aggregate metrics, and classified errors, and verify a sample JSON output can be parsed by tests.
- [x] 6.4 Add `data/experiments/` with an experiment record template that captures hypothesis, dataset, retrieval settings, Qdrant index settings, reranker settings, LLM model/settings, system prompt reference, timestamp, metrics, detailed result paths, conclusion, and next action, and verify the template exists.
- [x] 6.5 Add a versioned project file for the LLM system prompt and verify experiment records include its path or identifier and content hash when available.
- [x] 6.6 Add or update an evaluation command that can save a run under `data/experiments/<run-name>/` without overwriting previous runs, and verify it writes `experiment.md` and `results.json` for a sample run.
- [x] 6.7 Ensure experiments that change search strategy or Qdrant indexing record search text fields, payload fields, embedding model/dimension, collection/vector configuration, TOP-K/fusion settings, and rebuild source/date, and verify those fields appear in the experiment record.
- [x] 6.8 Run and record an MVP baseline experiment without hard pass/fail quality thresholds, and verify the experiment conclusion states accept, reject, or next experiment based on measured metrics.

## 7. Validation

- [x] 7.1 Run `python -m pytest -q` and verify the full test suite passes.
- [x] 7.2 If Qdrant and LLM credentials are available, run the README hybrid-rerank index/search smoke test and verify at least one `MATCHED` and one LLM-driven `NOT_FOUND` case can be observed.
- [x] 7.3 If Qdrant and LLM credentials are available, run one tracked evaluation experiment and verify its metrics and conclusion are saved under `data/experiments/`.
- [x] 7.4 Review the tracked evaluation output and verify `WRONG_NOT_FOUND` is reported as the primary business-risk metric separately from `FALSE_MATCH`.
- [x] 7.5 Run `openspec validate add-rag-business-mapping --strict` and verify the change artifacts pass validation.
