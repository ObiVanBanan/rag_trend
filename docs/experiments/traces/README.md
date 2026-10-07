# DEV stage traces

This directory stores **small, commit-safe DEV diagnostics** produced by
`scripts/eval_competitor_analogs.py --public-trace-output ...`.

These artifacts exist so later work can answer *where* a benchmark case failed
without guessing from aggregate metrics.

## What a public trace contains

For each DEV query:

- resolver status and canonical facts;
- retrieval query / alternate query;
- dense and BM25 candidate ranks;
- the full merged RRF pool **before final top-k truncation**;
- initial top-k candidates;
- hard-filter checks for those candidates;
- reranker input IDs;
- reranker status, reasons and selections;
- benchmark-derived stage labels such as:
  - `MODALITY_POOL_MISS`
  - `RRF_TRUNCATION`
  - `HARD_FILTER:<field>`
  - `RERANK_NOT_SELECTED`

It may contain the DEV query and candidate LD IDs.

## What must never be committed

Public traces must **not** contain:

- `acceptable_ld_ids`;
- acceptable LD articles/names;
- raw GOLD rows;
- final unseen holdout labels or traces.

The full evaluator output contains GOLD information and defaults to `.tmp/`.
Keep it local.

## Recommended command

```powershell
uv run python scripts/eval_competitor_analogs.py \
  --sample 100 \
  --seed 53 \
  --workers 1 \
  --output .tmp/e07-full-gold.json \
  --public-trace-output docs/experiments/traces/E07-seed53.json
```

Then add only the public trace plus a short result entry to
`docs/EXPERIMENTS.md`.

## Leakage boundary

The inspected frozen-100 is a **DEV benchmark**, so stage labels are allowed in
this directory. Never generate or commit a public trace for the final unseen
holdout. Final holdout evidence stays outside the repository until champion
evaluation is complete.
