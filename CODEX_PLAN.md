# CODEX PLAN — RAG Wave 1: technical lexical normalization experiment

## Goal

Stop working on the autonomous Codex-loop hardening for now. The user explicitly deferred that work.

Move back to the actual nomenclature RAG and run the first evidence-based retrieval improvement experiment.

The known concrete retrieval failure is `v2_q09`:

```text
Кран шаровый латунь 11б27п1 Ду20 вр/нр Ру40, в помещении, внутренняя/наружная резьба
```

Strict golden match recovered only in expanded review:

```text
ld_id = 17889
Кран шаровой латунный LD Pride 47.20.В-Н.Б Ду 20 Ру 40 ...
```

The current lexical pipeline tokenizes technical equivalents differently:

```text
query:    Ду20 / Ру40 / ВР / НР
catalog:  DN 20 / PN 4,0 / Внутренняя / Наружная
```

Current `tokenize()` treats these as unrelated tokens. This wave tests whether a small deterministic technical-token augmentation fixes this class of failure.

This is an **eval-only experiment**. Do NOT modify production BM25/Hybrid behavior in this wave.

---

# Scope rules

## Do

- use `data/eval_queries_v2.json`;
- use `data/eval_labels_v2.json`;
- evaluate only queries with:
  - `label_status == VERIFIED`
  - `expected_status == MATCHED`;
- build an offline BM25 baseline from `ld_products_full_nomenclature.csv`;
- compare current tokenization vs experimental technical normalization;
- generate a deterministic report under `data/`;
- add unit/regression tests.

## Do NOT

- modify `scripts/codex_loop.py`;
- create or modify `scripts/codex_loop_v2.py`;
- change Qdrant/dense retrieval;
- change production `BM25Store` behavior;
- change `HybridRetriever`;
- change RRF settings;
- change the DeepSeek reranker;
- change human-review state;
- change `data/eval_labels_v2.json`;
- finalize the unresolved NOT_FOUND queries;
- add an LLM query parser;
- add StructuredQuery;
- add hard filtering/rule-engine logic to production.

The unresolved NOT_FOUND ground-truth work does not block this wave because this experiment measures retrieval only on the currently VERIFIED MATCHED queries.

---

# Why start here

Current production BM25 is intentionally simple:

```python
query_tokens = tokenize(query)
```

and document tokens come from:

```python
tokenize(build_lexical_text(product))
```

This is good architecture, but technical notation aliases are currently disconnected.

Examples that should have shared lexical evidence:

```text
Ду20        <-> DN 20
Ру40        <-> PN 4,0 / 4.0 МПа
Ру16        <-> PN 1,6 / 1.6 МПа
ВР          <-> внутренняя резьба
НР          <-> наружная резьба
фланцевый   <-> фланцевое
муфтовый    <-> резьбовое
шаровый     <-> шаровой
```

Do not solve this with synonym expansion from an LLM. Use deterministic canonical technical tokens.

---

# Required implementation

## 1. Add an eval-only technical lexical normalizer

Create:

```text
src/nomenclature_matcher/eval_lexical_normalization.py
```

This module must NOT be wired into production code in this wave.

Expose something equivalent to:

```python
def technical_lexical_tokens(text: str) -> list[str]:
    ...
```

The returned tokens must contain:

1. all current raw tokens from `documents.tokenize(text)`;
2. additional canonical technical tokens derived deterministically from the text.

This is **augmentation**, not replacement. Raw lexical evidence must remain available.

### Required canonical groups

Implement at least the following.

### DN

Equivalent examples:

```text
Ду20
Ду 20
DN20
DN 20
```

must add:

```text
dn_20
```

Do the same generically for numeric DN values.

### PN / nominal pressure

Canonicalize common Russian/catalog notation to nominal-pressure class tokens.

Required equivalences:

```text
Ру16
PN16
1,6 МПа
1.6 MPa
PN 1,6        # catalog field currently stores MPa-like value
```

must share a canonical token such as:

```text
pn_16
```

And:

```text
Ру40
PN40
4,0 МПа
4.0 MPa
PN 4,0
```

must share:

```text
pn_40
```

Do not hardcode only 16 and 40. Implement a small generic parser/converter.

Be conservative around malformed numbers.

### Thread direction

Required aliases:

```text
ВР
внутренняя резьба
Тип резьбы: Внутренняя
```

add:

```text
thread_internal
```

And:

```text
НР
наружная резьба
Тип резьбы: Наружная
```

add:

```text
thread_external
```

Combined forms such as:

```text
ВР/НР
В-Н
Внутренняя/Наружная
```

should add both tokens when the meaning is clear.

Do **not** interpret generic location wording like `наружный (в колодце)` as an external thread.

### Joining type

Add conservative canonical aliases:

```text
фланцевый / фланцевое     -> join_flanged
межфланцевый               -> join_wafer
муфтовый / резьбовое       -> join_threaded
приварной / приварное      -> join_welded
компрессионный / обжимной  -> join_compression
```

`межфланцевый` must NOT also become ordinary `join_flanged` merely because the word contains `фланц`.

### Ball-valve morphology

When text clearly contains a ball valve phrase, normalize:

```text
кран шаровый
кран шаровой
```

to an additional token such as:

```text
product_ball_valve
```

Do not build a broad product taxonomy in this wave.

### Material warning

Do **not** infer body material from arbitrary morphology such as the word `стальной` anywhere in the string.

We already saw the false-positive pattern:

```text
Кран шаровой латунный ... рычаг стальной
```

This must never become `material_steel` just because the lever is steel.

It is acceptable to skip material canonicalization entirely in this wave.

---

## 2. Add an experimental BM25 implementation/helper

Do not modify production `BM25Store`.

Create an eval-only helper, either in the same module or a separate module such as:

```text
src/nomenclature_matcher/eval_bm25.py
```

It should build BM25 over the exact same products and exact same `build_lexical_text(product)` documents, but tokenize both corpus and query with:

```python
technical_lexical_tokens(...)
```

The experiment must otherwise stay comparable to current BM25:

- same product set;
- same BM25 implementation/parameters;
- positive-score-only results;
- same ranking semantics.

Do not add field boosts, filters or hand-tuned score bonuses in this wave.

---

## 3. Add a V2 offline BM25 experiment script

Create:

```text
scripts/eval_v2_bm25_normalization.py
```

This script must require no OpenAI, DeepSeek, Qdrant or network access.

Inputs:

```text
ld_products_full_nomenclature.csv
data/eval_queries_v2.json
data/eval_labels_v2.json
```

Evaluate only VERIFIED MATCHED queries.

For every query run:

```text
A. current BM25Store, limit=100
B. experimental normalized BM25, limit=100
```

For both variants record:

- acceptable LD IDs;
- first relevant rank;
- relevant IDs found in TOP100;
- hit@1;
- hit@3;
- hit@5;
- hit@10;
- hit@20;
- hit@50;
- hit@100;
- top 20 candidate IDs/names with score.

Also record for each query:

```text
rank_delta = current_first_relevant_rank - normalized_first_relevant_rank
```

Use `null` cleanly when there is no hit in TOP100.

---

## 4. Produce aggregate comparison metrics

Output:

```text
data/eval_v2_bm25_normalization_results.json
```

Include at minimum:

```text
verified_matched_queries
current_bm25:
  recall_at_1
  recall_at_3
  recall_at_5
  recall_at_10
  recall_at_20
  recall_at_50
  recall_at_100
normalized_bm25:
  same metrics
queries_improved
queries_unchanged
queries_worsened
```

Also include a clear dedicated q09 diagnostic:

```text
v2_q09:
  golden_ids
  current_first_relevant_rank
  normalized_first_relevant_rank
  current_hit_at_20
  normalized_hit_at_20
```

Print a concise console summary too.

---

## 5. Keep the comparison honest

The experiment must not special-case query IDs or golden LD IDs during ranking.

Forbidden examples:

```python
if query_id == "v2_q09": ...
if ld_id == 17889: score += ...
```

Golden IDs may only be used **after ranking** to calculate metrics.

The normalizer must operate on text, not Eval IDs.

---

# Tests

Add focused tests, for example:

```text
tests/test_eval_lexical_normalization.py
```

At minimum cover:

## DN aliases

```text
Ду20 -> dn_20
DN 20 -> dn_20
```

## PN aliases

```text
Ру16 -> pn_16
PN16 -> pn_16
1,6 МПа -> pn_16
PN 1,6 -> pn_16

Ру40 -> pn_40
4,0 МПа -> pn_40
PN 4,0 -> pn_40
```

## Thread aliases

```text
ВР/НР -> thread_internal + thread_external
Внутренняя/Наружная -> both
```

and verify:

```text
наружный (в колодце)
```

does NOT by itself add `thread_external`.

## Joining aliases

Verify:

```text
фланцевый -> join_flanged
межфланцевый -> join_wafer
```

and `межфланцевый` does not accidentally become `join_flanged`.

## Product morphology

```text
кран шаровый
кран шаровой
```

must share `product_ball_valve`.

## Material false positive regression

For:

```text
Кран шаровой латунный LD Pride Ду32 Ру25 рычаг стальной
```

the normalizer must not manufacture a steel-body/material token.

## Raw-token preservation

All tokens from current `documents.tokenize(text)` must still be present in the augmented token list.

## Ranking regression fixture

Build a tiny synthetic corpus where a query using:

```text
Ду20 Ру40 ВР/НР
```

must rank a document containing:

```text
DN 20
PN 4,0
Тип резьбы: Внутренняя/Наружная
```

above technically contradictory distractors after normalization.

Do not use real network/services in tests.

---

# Validation

Run:

```bash
python -m pytest -q
```

Then run the offline experiment:

```bash
python scripts/eval_v2_bm25_normalization.py
```

The experiment is local/offline and should work without service credentials.

Commit the generated result:

```text
data/eval_v2_bm25_normalization_results.json
```

Do not hand-edit the result.

---

# Acceptance criteria

This wave is considered technically successful if all are true:

1. production retrieval code is unchanged;
2. current BM25 baseline is measured from the real catalog;
3. experimental normalized BM25 is measured on the same real catalog;
4. no VERIFIED MATCHED query that currently hits BM25@20 becomes a miss at @20;
5. normalized BM25 Recall@20 is >= current BM25 Recall@20;
6. `v2_q09` improves materially;
7. target success for q09 is:

```text
normalized_first_relevant_rank <= 20
```

If q09 does not reach TOP20, do NOT keep adding ad-hoc aliases in the same wave. Preserve the result and report that the experiment was insufficient.

This is an experiment. Even if all metrics improve, **do not promote the normalizer into production BM25 in this commit**.

---

# Expected files

Expected new files:

```text
src/nomenclature_matcher/eval_lexical_normalization.py
src/nomenclature_matcher/eval_bm25.py          # optional if split is cleaner
scripts/eval_v2_bm25_normalization.py
tests/test_eval_lexical_normalization.py
data/eval_v2_bm25_normalization_results.json
```

`CODEX_PLAN.md` is protected by the loop and must not be modified by Codex.

Do not modify:

```text
scripts/codex_loop.py
src/nomenclature_matcher/bm25_store.py
src/nomenclature_matcher/hybrid_retriever.py
src/nomenclature_matcher/settings.py
data/eval_labels_v2.json
data/eval_v2_human_review.json
data/eval_v2_retrieval_miss_human_review.json
```

---

# Definition of Done

- deterministic technical token augmentation exists;
- raw tokens are preserved;
- DN/PN/thread/joining aliases are covered by tests;
- material false-positive regression is covered;
- current vs normalized BM25 are evaluated offline on all VERIFIED MATCHED Eval V2 queries;
- q09 diagnostic is explicit;
- result JSON is generated by the script;
- pytest passes;
- no production retrieval change is made;
- no Codex-loop work is performed.

STOP after this experiment. The next wave will be chosen from the measured result:

- promote lexical normalization to production if it clearly wins;
- otherwise investigate another retrieval layer (dense text construction / candidate limits / fusion) based on evidence.

---

# Final report

Return only:

1. changed files;
2. pytest result;
3. current BM25 Recall@20;
4. normalized BM25 Recall@20;
5. q09 current first relevant rank;
6. q09 normalized first relevant rank;
7. whether any VERIFIED MATCHED query regressed at @20;
8. conclusion: `PROMISING` or `INSUFFICIENT`.
