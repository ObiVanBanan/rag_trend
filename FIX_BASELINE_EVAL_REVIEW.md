# Fix plan: Baseline Eval review findings

## Goal

Fix the evaluation layer only.

Do **not** change retrieval, BM25, RRF, embeddings, DeepSeek prompt, Qdrant settings, or product matching logic in this wave.

The current hybrid architecture is already sufficient for the next experiment. The problem is that the current baseline can report misleading metrics because the golden labels are not trustworthy and the error classification semantics are wrong.

This task is written for a weaker Codex model. Keep changes small and explicit.

---

# Finding 1 — current golden labels are not actually golden

`data/eval_labels.json` currently contains acceptable product IDs and business judgments that were generated automatically rather than verified by a human/domain owner.

This invalidates the baseline.

Examples that must NOT be treated as trusted ground truth:

- `q03` query is a generic steel ball valve DN100 PN25, but the current label explicitly accepts underground welded variants. Extra specialization was not requested.
- `q04` accepts variants with drives/high-temperature execution even though the generic query does not request those options.
- `q07` query is `Электропривод для задвижки AUMA`, but current acceptable IDs include complete AUMA-driven **ball valves**, not a drive for a gate valve.
- `q06` and `q10` are marked `NOT_FOUND` without a human-verified catalog decision.
- Generic queries such as `q05` cannot receive arbitrary product IDs just because they seem plausible.

Do not attempt to fix these labels by guessing better IDs.

## Required change

Change label semantics so that only **human-verified** labels participate in metrics.

Recommended format:

```json
{
  "q01": {
    "label_status": "UNREVIEWED",
    "acceptable_ld_ids": [],
    "expected_status": null,
    "human_comment": ""
  }
}
```

A reviewed query:

```json
{
  "qXX": {
    "label_status": "VERIFIED",
    "acceptable_ld_ids": [123, 456],
    "expected_status": "MATCHED",
    "human_comment": "Verified manually"
  }
}
```

A verified NOT_FOUND query:

```json
{
  "qXX": {
    "label_status": "VERIFIED",
    "acceptable_ld_ids": [],
    "expected_status": "NOT_FOUND",
    "human_comment": "Verified manually: no suitable LD product"
  }
}
```

## Important

For the current file, do NOT preserve automatically invented product IDs as `acceptable_ld_ids`.

Set queries that were not explicitly human-verified to `UNREVIEWED`.

It is acceptable if the first trustworthy baseline has very few labeled queries. Correct labels are more important than a larger number.

The eval script must still run retrieval for unreviewed queries and save their candidate lists, but they must not affect accuracy/recall metrics.

---

# Finding 2 — error classification is semantically wrong

Current `classify_error_type()` checks failures of individual retrievers before checking whether the final hybrid pipeline succeeded.

This causes a case like:

```text
dense_hit = false
bm25_hit = true
hybrid_hit = true
reranker_success = true
```

to become:

```text
DENSE_RETRIEVAL_FAIL
```

That is wrong.

The final hybrid system succeeded. Dense miss is useful diagnostic information, not a final pipeline error.

This is especially important for the brass DN25 regression case, where BM25 was added specifically to recover candidates that dense retrieval misses.

## Required classification logic

For `expected_status == MATCHED`:

```text
if acceptable product is NOT in Hybrid TOP-20:
    HYBRID_RETRIEVAL_FAIL

elif acceptable product is in Hybrid TOP-20 but DeepSeek does not select an acceptable product:
    RERANKER_FAIL

else:
    OK
```

Keep these fields separately:

```text
dense_hit
bm25_hit
hybrid_hit
```

Do NOT convert `dense_hit=false` or `bm25_hit=false` into final `error_type` when the hybrid pipeline succeeds.

For verified `NOT_FOUND`:

```text
DeepSeek NOT_FOUND -> CORRECT_NOT_FOUND
DeepSeek MATCHED   -> WRONG_NOT_FOUND
RERANK_FAILED      -> RERANKER_ERROR
```

For expected MATCHED and `RERANK_FAILED`:

```text
RERANKER_ERROR
```

Add `RERANKER_ERROR` rather than hiding API/JSON failure inside `RERANKER_FAIL` or `WRONG_NOT_FOUND`.

---

# Finding 3 — metrics must only use VERIFIED labels

Update all evaluation metric helpers.

Metrics must use:

```python
item["label_status"] == "VERIFIED"
```

Only verified `MATCHED` queries participate in:

```text
Dense Recall@20
BM25 Recall@20
Hybrid Recall@20
```

Only verified queries participate in:

```text
Reranker accuracy
```

`reranker_accuracy_given_hybrid_hit` should use verified MATCHED queries where `hybrid_hit == true`.

Do not silently default missing labels to `MATCHED`.

Current behavior:

```python
expected_status = label.get("expected_status", "MATCHED")
```

must be removed.

If label is missing/unreviewed:

```text
label_status = UNREVIEWED
expected_status = null
```

and no metric/error classification should be generated from it.

Suggested per-query output for unreviewed cases:

```json
{
  "label_status": "UNREVIEWED",
  "error_type": "UNREVIEWED",
  "dense_hit": null,
  "bm25_hit": null,
  "hybrid_hit": null,
  "reranker_success": null
}
```

Still save all retrieval and DeepSeek output for manual review.

---

# Finding 4 — there is no committed baseline result artifact

The latest commit is named `Add baseline evaluation report`, but the repository does not contain a generated baseline results artifact.

Do NOT commit a fake report based on unverified labels.

After the label semantics above are fixed, `scripts/eval.py` should generate:

```text
data/eval_results.json
```

This remains a generated file during development.

Once a set of labels is manually VERIFIED and the user explicitly runs/accepts the eval, create a frozen snapshot such as:

```text
data/baselines/baseline_v1.json
```

Do not create `baseline_v1.json` in this fix wave unless verified labels and an actual eval run exist.

---

# Keep these parts — they are correct

Do not undo the good changes already made:

- `rerank_candidates(query, candidates)` exists and allows eval to rerank the exact saved Hybrid TOP-20.
- selected candidate scores are split into `dense_score`, `bm25_score`, `rrf_score`, `llm_confidence`.
- do not restore the ambiguous `vector_score` field.
- `properties_json -> search_text` fix must remain.
- Dense + BM25 + RRF retrieval must remain unchanged.
- DeepSeek thinking remains disabled.
- embedding retry handling is out of scope for this fix unless tests are currently failing because of it.

---

# Tests to add/update

## 1. Hybrid success despite dense failure

```text
expected MATCHED
dense_hit = false
bm25_hit = true
hybrid_hit = true
reranker_success = true
```

Expected:

```text
error_type == OK
```

## 2. Hybrid success despite BM25 failure

```text
dense_hit = true
bm25_hit = false
hybrid_hit = true
reranker_success = true
```

Expected:

```text
error_type == OK
```

## 3. Hybrid retrieval failure

```text
hybrid_hit = false
```

Expected:

```text
HYBRID_RETRIEVAL_FAIL
```

## 4. Reranker failure

```text
hybrid_hit = true
reranker_success = false
DeepSeek completed normally
```

Expected:

```text
RERANKER_FAIL
```

## 5. Reranker technical failure

```text
DeepSeek status = RERANK_FAILED
```

Expected:

```text
RERANKER_ERROR
```

## 6. Unreviewed query exclusion

Dataset contains one VERIFIED and one UNREVIEWED query.

Verify:

- recall denominator contains only VERIFIED MATCHED query;
- reranker accuracy contains only VERIFIED query;
- UNREVIEWED query is still present in result JSON;
- UNREVIEWED query has no success/failure metric classification.

## 7. No default MATCHED label

A missing label entry must not become `expected_status=MATCHED`.

---

# Validation commands

Use the project's existing environment.

Run at minimum:

```bash
pytest
```

Then run:

```bash
python scripts/eval.py
```

If external API credentials are unavailable, unit tests must still pass using mocks. Do not weaken tests just to avoid API calls.

Report:

- files changed;
- test command and result;
- number of VERIFIED labels used in metrics;
- number of UNREVIEWED queries;
- do not claim baseline quality until human labels are verified.

---

# Definition of Done

This fix is complete when:

- automatically invented golden labels are no longer counted as truth;
- every label has explicit `label_status`;
- UNREVIEWED queries are excluded from metrics;
- missing labels never default to MATCHED;
- final `error_type` describes final hybrid pipeline failure, not individual retriever misses;
- `dense_hit` and `bm25_hit` remain diagnostic fields;
- `RERANKER_ERROR` distinguishes technical LLM failure;
- tests cover dense-only miss recovered by BM25;
- tests cover BM25-only miss recovered by dense;
- eval still reuses the exact same Hybrid TOP-20 for DeepSeek;
- score semantics remain separated;
- `pytest` passes.

After this fix STOP.

The next step is manual verification of the small eval dataset. Do not add retrieval features until trustworthy baseline labels exist.
