# Next Step: Baseline Eval for Hybrid Retrieval + DeepSeek

## Goal

Do not add more retrieval logic yet.

The current architecture is:

```text
query
  ├─> OpenAI dense embedding -> Qdrant TOP-N
  └─> BM25 TOP-N
          ↓
        RRF
          ↓
    Hybrid TOP-20
          ↓
      DeepSeek
          ↓
 MATCHED / NOT_FOUND
```

The next task is to measure how well this pipeline works on a small fixed golden dataset and identify the remaining failure modes.

This work is intended for a weaker Codex model. Keep changes small and do not redesign the project.

---

## Scope boundaries

Do NOT add during this wave:

- LLM attribute extraction;
- StructuredQuery;
- DN/PN hard filters;
- material compatibility rules;
- synonyms;
- query expansion;
- fuzzy matching;
- stemming/morphology;
- Qdrant sparse vectors;
- new embedding models;
- new rerankers;
- LangChain/LangGraph;
- API/UI.

Only build a trustworthy evaluation loop for the existing pipeline.

---

## Wave 1. Freeze the evaluation dataset

Use `data/eval_queries.json` as the fixed query set.

Keep the current real tender-like examples, including the regression case:

```text
Кран латунный шаровой муфтовый Д25
```

Do not modify query wording during an eval run.

Target size for this wave: 10–15 queries.

The dataset should contain a mix of:

- precise valve queries;
- competitor-brand naming;
- alternative DN/PN notation;
- material requirements;
- joining-type requirements;
- short/generic queries;
- non-valve products;
- at least one expected NOT_FOUND case.

---

## Wave 2. Complete golden labels

Use `data/eval_labels.json`.

For every query, fill an explicit set of acceptable LD product IDs.

Example:

```json
{
  "q11": {
    "acceptable_ld_ids": [1715, 199, 1005, 155, 197],
    "expected_status": "MATCHED",
    "human_comment": "Допустимы латунные резьбовые краны DN25"
  }
}
```

For a query where no LD product should be returned:

```json
{
  "qXX": {
    "acceptable_ld_ids": [],
    "expected_status": "NOT_FOUND",
    "human_comment": "В каталоге нет подходящего продукта"
  }
}
```

Do not use one single exact article when several products are legitimately acceptable.

---

## Wave 3. Separate retrieval metrics from reranker metrics

For each query calculate retrieval success independently for:

```text
Dense TOP-20
BM25 TOP-20
Hybrid TOP-20
```

A retrieval hit means:

```python
bool(actual_ld_ids & acceptable_ld_ids)
```

For MATCHED queries calculate:

```text
Dense Recall@20
BM25 Recall@20
Hybrid Recall@20
```

For expected NOT_FOUND queries, do not include them in Recall@20 denominator.

---

## Wave 4. Correct reranker evaluation

Do not measure success by counting `status == MATCHED`.

DeepSeek reranker success for an expected MATCHED query means:

```python
selected_ld_ids intersects acceptable_ld_ids
```

For an expected NOT_FOUND query, success means:

```text
DeepSeek status == NOT_FOUND
```

Calculate:

```text
Reranker accuracy
```

Also calculate conditional accuracy:

```text
Reranker accuracy when Hybrid TOP-20 contains an acceptable candidate
```

This separates retrieval failures from reranker failures.

---

## Wave 5. Reuse the exact same Hybrid TOP-20 for reranking

Current eval must not independently execute Hybrid retrieval twice for the same query.

Desired flow:

```text
query
  ↓
hybrid_candidates = hybrid_retriever.search(...)
  ↓
save Hybrid TOP-20
  ↓
pass EXACT hybrid_candidates to DeepSeek
```

Add a small matcher/helper method if needed, for example:

```python
rerank_candidates(query, candidates)
```

Do not perform a second embedding/Qdrant/BM25 retrieval just to call DeepSeek.

Reason:

- evaluation must compare DeepSeek against exactly the saved candidate list;
- avoid duplicate OpenAI embedding requests;
- avoid unnecessary Qdrant calls;
- make runs reproducible.

---

## Wave 6. Keep score semantics explicit

Do not call RRF score `vector_score`.

For selected candidates preserve separate fields:

```text
dense_score
bm25_score
rrf_score
llm_confidence
```

`llm_confidence` is only model-reported confidence and must not be treated as a calibrated probability.

Dense-only mode may continue exposing cosine similarity as `dense_score`.

---

## Wave 7. Save per-query error classification

For every query classify the final result automatically where possible:

```text
OK
DENSE_RETRIEVAL_FAIL
BM25_RETRIEVAL_FAIL
HYBRID_RETRIEVAL_FAIL
RERANKER_FAIL
CORRECT_NOT_FOUND
WRONG_NOT_FOUND
```

Priority logic for expected MATCHED queries:

```text
acceptable not in Hybrid TOP-20
    -> HYBRID_RETRIEVAL_FAIL

acceptable in Hybrid TOP-20 but DeepSeek did not select it
    -> RERANKER_FAIL

acceptable selected
    -> OK
```

Also keep dense/BM25 hit booleans separately for analysis.

---

## Wave 8. Produce one eval report

`python scripts/eval.py` should write a machine-readable result file and print a short summary.

Suggested summary:

```text
Queries: 11
Expected MATCHED: 9
Expected NOT_FOUND: 2

Dense Recall@20: 55.6%
BM25 Recall@20: 77.8%
Hybrid Recall@20: 88.9%

Reranker accuracy: 81.8%
Reranker accuracy given retrieval hit: 90.0%

Errors:
- HYBRID_RETRIEVAL_FAIL: 1
- RERANKER_FAIL: 1
- WRONG_NOT_FOUND: 0
```

Do not add dashboards or plotting yet.

---

## Wave 9. Preserve detailed outputs

For every query save:

```json
{
  "id": "q11",
  "query": "...",
  "expected_status": "MATCHED",
  "acceptable_ld_ids": [1715, 199, 1005, 155, 197],
  "dense_top20": [],
  "bm25_top20": [],
  "hybrid_top20": [],
  "deepseek_result": {},
  "dense_hit": false,
  "bm25_hit": true,
  "hybrid_hit": true,
  "reranker_success": true,
  "error_type": "OK"
}
```

This file is the baseline artifact for future comparisons.

---

## Wave 10. Tests

Add only focused tests.

### Recall calculation

Verify that only expected MATCHED queries contribute to Recall@20.

### Multiple acceptable IDs

If any one acceptable ID appears in TOP-20, retrieval is successful.

### NOT_FOUND

Correct NOT_FOUND counts as reranker success.

### Reranker failure

If acceptable candidate is in Hybrid TOP-20 but selected candidate is wrong, classify as `RERANKER_FAIL`.

### Hybrid failure

If acceptable candidate is missing from Hybrid TOP-20, classify as `HYBRID_RETRIEVAL_FAIL`.

### Candidate reuse

Verify eval passes the exact already-retrieved Hybrid candidate list to reranker and does not execute a second retrieval.

---

## Definition of Done

This wave is complete when:

- 10–15 queries are frozen;
- every query has a golden label;
- multiple acceptable products are supported;
- expected NOT_FOUND is supported;
- Dense Recall@20 is calculated correctly;
- BM25 Recall@20 is calculated correctly;
- Hybrid Recall@20 is calculated correctly;
- DeepSeek accuracy is based on selected LD IDs, not merely MATCHED status;
- reranker conditional accuracy is available;
- the same Hybrid TOP-20 is reused for DeepSeek;
- dense/BM25/RRF scores remain separate;
- each error is classified;
- one baseline JSON report is produced;
- tests pass.

After this wave STOP.

Do not improve retrieval until the baseline report has been manually reviewed.

The next architectural change must be chosen from observed error patterns, not assumptions.
