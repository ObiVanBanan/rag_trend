# Review feedback for commit 75dedce

This file contains the concrete reviewer findings for the current baseline/eval implementation.

Use it together with `FIX_BASELINE_EVAL_REVIEW.md`.

The next coding task is to fix these findings only. Do not redesign retrieval.

---

## Review verdict

The evaluation implementation is structurally improved, but the baseline is **not trustworthy yet**.

The main problem is not Dense/BM25/RRF/DeepSeek. The problem is the evaluation ground truth and error semantics.

Do not add new retrieval features in this fix.

---

# P0 — current golden labels contain unverified and demonstrably wrong business judgments

`data/eval_labels.json` was populated automatically. These labels must not be treated as verified truth.

Do not try to improve the automatically chosen IDs by guessing more IDs.

Mark unverified queries as `UNREVIEWED` and exclude them from metrics until a human/domain owner verifies them.

## Concrete confirmed examples

### q03

Query:

```text
Кран шаровой стальной DN100 PN25
```

Current label accepts ID `10395`.

Catalog inspection shows ID `10395` is:

```text
Кран шаровый LD для подземной установки ... Ду100 Ру2,5МПа ...
Присоединение: Приварное
Тип продукта: Кран шаровой для подземной установки
```

The query does not request underground installation or welded connection.

Therefore ID `10395` must NOT be treated as trusted ground truth without explicit human approval.

The current label comment even says underground variants are acceptable. That business rule was invented by the coding agent and was never approved.

---

### q04

Query:

```text
Затвор дисковый межфланцевый Ду150 Ру16
```

Current label accepts ID `2556`.

Catalog inspection shows ID `2556` is:

```text
Поворотно-дисковый затвор 3-х эксцентриковый ... под электропривод
DN: 150
PN: 1,6
Присоединение: Фланцевое
Управление: Под электропривод
```

The query asks for a **межфланцевый** valve, while this catalog item is marked **Фланцевое** and additionally has a special drive execution.

Therefore this ID is not a safe golden answer unless manually verified.

---

### q07

Query:

```text
Электропривод для задвижки AUMA
```

Current label accepts ID `15438` and related IDs.

Catalog inspection shows ID `15438` is not a standalone actuator for a gate valve. It is a complete product:

```text
Кран шаровый LD ... Ду100 Ру1,6МПа ... с электроприводом AUMA
Тип продукта: Кран шаровой
```

So the current golden label evaluates a query for an actuator for a **gate valve** against complete **ball valves with AUMA actuators**.

This is a clear label error.

Do not count q07 in metrics until manually verified.

---

### q06 and q10

They are currently marked `NOT_FOUND`.

Those NOT_FOUND decisions were not human verified.

A coding agent must not decide that a catalog has no acceptable product based only on intuition or the current retrieval output.

Mark them `UNREVIEWED` until manually checked.

---

### q11

Query:

```text
Кран латунный шаровой муфтовый Д25
```

ID `1715` was manually investigated earlier and is a plausible positive example:

```text
Кран шаровой латунный LD Pride ... Ду25 Ру40
Присоединение: Резьбовое
Материал корпуса: Латунь ЛС59-1
```

However, do not automatically assume every other ID currently listed beside it is verified unless explicitly confirmed.

The code must support partially reviewed datasets cleanly.

---

# P1 — final error classification is wrong

Current `classify_error_type()` reports an individual retriever miss as the final pipeline failure even when hybrid retrieval and reranking succeed.

Example:

```text
dense_hit = false
bm25_hit = true
hybrid_hit = true
reranker_success = true
```

Current behavior can return:

```text
DENSE_RETRIEVAL_FAIL
```

This is incorrect.

The whole point of hybrid retrieval is that BM25 can recover a Dense miss and Dense can recover a BM25 miss.

Correct final classification for this case:

```text
OK
```

Keep these as diagnostics only:

```text
dense_hit
bm25_hit
```

Final pipeline classification for expected MATCHED queries must be:

```text
if hybrid_hit == false:
    HYBRID_RETRIEVAL_FAIL
elif DeepSeek technical failure:
    RERANKER_ERROR
elif reranker_success == false:
    RERANKER_FAIL
else:
    OK
```

Do not report `DENSE_RETRIEVAL_FAIL` or `BM25_RETRIEVAL_FAIL` as final `error_type` when Hybrid TOP-20 contains a valid candidate and DeepSeek selects it correctly.

---

# P1 — missing/unreviewed labels must never default to MATCHED

Current eval code uses behavior equivalent to:

```python
expected_status = label.get("expected_status", "MATCHED")
```

This creates fake ground truth.

Required behavior:

```text
missing label -> UNREVIEWED
unverified label -> UNREVIEWED
```

For UNREVIEWED queries:

- still run Dense retrieval;
- still run BM25 retrieval;
- still run Hybrid retrieval;
- still run DeepSeek;
- save all candidates and model output;
- do NOT include query in Recall/Accuracy denominators;
- do NOT assign success/failure based on golden truth;
- use `error_type = UNREVIEWED`.

---

# P1 — technical DeepSeek errors must be separated from quality failures

If DeepSeek/API/JSON fails and matcher returns:

```text
RERANK_FAILED
```

classify it as:

```text
RERANKER_ERROR
```

Do not classify it as `RERANKER_FAIL`, `WRONG_NOT_FOUND`, or normal matching quality failure.

We need to distinguish:

```text
model selected wrong candidate
```

from:

```text
model/API did not produce a usable answer
```

---

# Keep the good changes

Do not undo these improvements from the recent commits:

- `rerank_candidates(query, candidates)` must remain;
- eval should pass the exact already-saved Hybrid TOP-20 into DeepSeek;
- no second Hybrid retrieval should be performed just for reranking;
- `dense_score`, `bm25_score`, `rrf_score`, and `llm_confidence` must stay separate;
- do not restore `vector_score` as an ambiguous field;
- Dense + BM25 + RRF architecture stays unchanged;
- DeepSeek reranker stays unchanged in this wave;
- the `properties_json -> search_text` fix stays.

---

# Required tests

Add/update tests that explicitly prove the following.

## Test A — Dense miss recovered by BM25

```text
expected_status = MATCHED
dense_hit = false
bm25_hit = true
hybrid_hit = true
reranker_success = true
```

Expected:

```text
error_type = OK
```

## Test B — BM25 miss recovered by Dense

```text
expected_status = MATCHED
dense_hit = true
bm25_hit = false
hybrid_hit = true
reranker_success = true
```

Expected:

```text
error_type = OK
```

## Test C — true hybrid retrieval failure

```text
expected_status = MATCHED
hybrid_hit = false
```

Expected:

```text
HYBRID_RETRIEVAL_FAIL
```

## Test D — reranker quality failure

```text
expected_status = MATCHED
hybrid_hit = true
DeepSeek completed normally
reranker_success = false
```

Expected:

```text
RERANKER_FAIL
```

## Test E — reranker technical error

```text
DeepSeek/matcher status = RERANK_FAILED
```

Expected:

```text
RERANKER_ERROR
```

## Test F — unreviewed query

A query with no verified label must:

- remain in eval output;
- have `label_status = UNREVIEWED`;
- have `error_type = UNREVIEWED`;
- not affect retrieval recall;
- not affect reranker accuracy.

---

# Acceptance criteria

This fix is accepted only when:

1. No automatically invented acceptable IDs are counted as verified truth.
2. `data/eval_labels.json` supports explicit `VERIFIED` / `UNREVIEWED` status.
3. Unreviewed labels are excluded from all quality metrics.
4. Missing labels never default to MATCHED.
5. Dense/BM25 misses are diagnostics, not final errors when hybrid succeeds.
6. `RERANKER_ERROR` exists for technical model/API failures.
7. The exact Hybrid TOP-20 is reused for DeepSeek.
8. Retrieval architecture is not modified.
9. Unit tests pass.
10. No claim such as "baseline quality is X%" is made until human verification is complete.

Run:

```bash
pytest
```

If API credentials are available, also run:

```bash
python scripts/eval.py
```

At the end report:

- files changed;
- tests run and result;
- VERIFIED query count;
- UNREVIEWED query count;
- whether any retrieval/reranker code was changed (expected: no).

After this fix STOP. Do not implement new retrieval improvements.