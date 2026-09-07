# Experiment: 2026-09-07-mvp-baseline

## Hypothesis

MVP baseline for hybrid retrieval plus LLM candidate selection before hard quality thresholds.

## Dataset

- Path: /workspaces/ML-Cancer-Data/data/eval_queries.json
- Labels: /workspaces/ML-Cancer-Data/data/eval_labels.json

## Configuration

```json
{
  "retrieval": {
    "hybrid_dense_limit": 50,
    "hybrid_bm25_limit": 50,
    "hybrid_rerank_limit": 20,
    "rrf_k": 60,
    "rerank_result_limit": 3
  },
  "qdrant_index": {
    "collection_alias": "steel_products_active",
    "dense_vector_name": "dense",
    "embedding_model": "text-embedding-3-small",
    "embedding_dimension": 1536,
    "search_text_fields": [
      "name",
      "article",
      "dn",
      "pn",
      "joining_type",
      "properties_json"
    ],
    "payload_fields": [
      "ld_id",
      "name",
      "article",
      "price",
      "dn",
      "pn",
      "joining_type",
      "url",
      "properties",
      "search_text"
    ]
  },
  "llm": {
    "model": "deepseek-v4-flash",
    "base_url": "https://api.deepseek.com/v1",
    "timeout_seconds": 20.0,
    "temperature": 0
  },
  "system_prompt": {
    "path": "src/nomenclature_matcher/prompts/reranker_system.md",
    "sha256": "c322011fdea16f0c11a783a4e9142641d2fd02a42194a0f4b310c9d6847c9423"
  }
}
```

## Metrics

```json
{
  "queries": 11,
  "verified_queries": 4,
  "unreviewed_queries": 7,
  "expected_matched": 4,
  "expected_not_found": 0,
  "dense_recall_at_5": 1.0,
  "bm25_recall_at_5": 0.0,
  "hybrid_recall_at_5": 0.75,
  "dense_recall_at_20": 1.0,
  "bm25_recall_at_20": 0.25,
  "hybrid_recall_at_20": 1.0,
  "final_selection_accuracy": 1.0,
  "reranker_accuracy": 1.0,
  "reranker_accuracy_given_hybrid_hit": 1.0,
  "wrong_not_found_rate": 0.0,
  "false_match_rate": null,
  "wrong_product_selection_rate": 0.0,
  "primary_business_risk_metric": "wrong_not_found_rate",
  "baseline_has_hard_quality_threshold": false,
  "error_counts": {
    "OK": 4,
    "UNREVIEWED": 7
  }
}
```

## Results

- Detailed results: results.json

## Conclusion

Baseline measured; review metrics before accepting or defining quality thresholds.

## Next Action

Inspect WRONG_NOT_FOUND, FALSE_MATCH, WRONG_LLM_SELECTION, and retrieval miss examples.
