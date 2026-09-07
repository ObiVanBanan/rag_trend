## Why

The project needs a business-facing MVP that maps incoming tender nomenclature strings to LD catalog products with an explicit `MATCHED` or `NOT_FOUND` decision. Tests showed that cosine similarity alone is not reliable enough for the business requirement, so the MVP must combine hybrid retrieval, reranking, and LLM-based candidate selection.

## What Changes

- Add a RAG-style mapping workflow for `list[str]` input that returns one result object per original input item.
- Use the prebuilt LD vector index and lexical LD catalog data for hybrid candidate retrieval without re-vectorizing the full LD catalog during user searches.
- Rerank retrieved candidates and use an LLM to choose the most suitable LD product for the original query or return `NOT_FOUND`.
- Preserve retrieved candidates, ranks, scores, LLM selection details, and extended LD fields for development review and quality evaluation.
- Keep the production-facing selected product payload minimal: LD name and article only.
- Keep duplicate input strings from causing duplicate search calls while preserving result order and cardinality.
- Add a quality evaluation workflow that measures retrieval and final LLM selection quality on reviewed examples.
- Add experiment tracking under `data/experiments/` for hypotheses, run configurations, metrics, results, and conclusions.
- Exclude structured query extraction, DN/PN hard filters, material compatibility rules, tender Excel processing, UI, and tender pipeline integration from this MVP.

## Capabilities

### New Capabilities

- `rag-business-mapping`: Hybrid retrieval, reranking, and LLM-assisted batch mapping from tender nomenclature strings to LD products according to the business requirements and quality findings.

### Modified Capabilities

- None.

## Impact

- Affected package code: `src/nomenclature_matcher/matcher.py`, `src/nomenclature_matcher/models.py`, `src/nomenclature_matcher/documents.py`, `src/nomenclature_matcher/indexer.py`, `src/nomenclature_matcher/qdrant_store.py`, `src/nomenclature_matcher/bm25_store.py`, `src/nomenclature_matcher/hybrid_retriever.py`, `src/nomenclature_matcher/reranker.py`, and settings related to retrieval limits, reranking, and LLM selection.
- Affected scripts: indexing/search CLI workflows may need batch-oriented output or a thin service entrypoint for validating the MVP contract.
- Affected evaluation artifacts: `data/eval_*` files and a new `data/experiments/` workflow for tracking hypotheses, experiment settings, metrics, and conclusions.
- Affected tests: focused pytest coverage for batch mapping, duplicate handling, hybrid candidate retrieval, reranker/LLM selection behavior, production/debug payloads, quality metrics, experiment record handling, and index/search text construction.
- External systems: PostgreSQL is the business source for LD products, while the current repository also supports CSV-based loading for local MVP validation; Qdrant remains the retrieval backend.
