# RAG research Harness v2

Autonomous optimization loop for `rag_trend` on the current tender workload.

The harness now treats `data/kontur_all_783_queries.json` as the primary optimization/research corpus while keeping labeled public GOLD and adaptive hidden validation as correctness/safety guardrails.

## Why this shape

The 783-row dataset is mostly unlabeled, so raw `MATCHED` count is not accuracy. The loop therefore does two different jobs:

1. **Labeled guardrails** answer: did we break correctness/safety?
2. **Current 783 workload** answers: did the candidate plausibly improve recurring real-world behavior?

The harness never accepts a candidate merely because it returns more products.

## Current loop

```text
CURRENT CHAMPION
   │
   ├─ one cached full 783 baseline
   │
   ▼
RESEARCHER (gpt-5.6-terra, medium)
   │  sees champion 783 summary + durable experiment memory
   ▼
PLANNER (gpt-5.6-terra, medium)
   │  one bounded falsifiable hypothesis
   ▼
IMPLEMENTER (gpt-5.5, low)
   │
   ▼
FULL TESTS
   │
   ▼
PUBLIC GOLD
   ├─ regression -> rollback
   ▼
ADAPTIVE HIDDEN
   ├─ regression -> rollback
   ▼
FULL 783 CANDIDATE RUN
   │  row-for-row comparison with champion
   │  stage transitions + changed products + hard-filter causes
   ▼
CHEAP VALIDATOR (gpt-5.5, low)
   ├─ REJECT -> rollback
   └─ ACCEPT -> new champion
```

The expensive 783 candidate run happens only after unit/public/hidden gates have survived. The champion 783 baseline is cached in external harness state and reused until a new champion is promoted.

## 783 diagnostics

For the current dataset the harness records:

- `QUERY_REJECTED`
- `HARD_CONSTRAINT_FILTER`
- `RERANK_NOT_FOUND`
- `RERANK_FAILED`
- `MATCHED`
- web/MCP attempted / accepted counts
- concrete first failing hard constraints (`dn`, `pn`, `joining_type`, `thread_type`, `body_material`, `bore_type`, `control`, etc.)
- row-level stage transitions
- changed returned LD products

Examples of how deltas are interpreted:

- `NOT_FOUND -> MATCHED`: potentially useful, but only if the returned LD product is technically defensible.
- `QUERY_REJECTED -> HARD_CONSTRAINT_FILTER`: useful diagnostic movement, not proof of correctness.
- `MATCHED -> NOT_FOUND`: explicit regression candidate for validator review.
- `MATCHED -> MATCHED` with another product: must be inspected as a changed-product risk.

## Supporting evidence

The older 253-row Kontur research corpus remains available for causal research:

- 90 high-confidence first-pass labels
- 130 unresolved/review cases
- 33 provisional `NOT_FOUND`

It is supporting evidence only; it is not the primary optimization corpus anymore.

## Models and budgets

Defaults are in `harness_rag/config.json`:

```text
Researcher / Planner:       gpt-5.6-terra, medium
Implementer:                gpt-5.5, low
Validator:                  gpt-5.5, low
Scientific iterations:      5 by default, max 7
Agent calls:                28
Research calls:             7
Validator calls:            7
Fixer calls:                0
Index rebuilds:             max 5
```

## Requirements

Before launch:

- branch must not be `main`/`master`;
- worktree must be clean;
- Qdrant must be running with the LD collection restored;
- `OPENAI_API_KEY` and `DEEPSEEK_API_KEY` must be available;
- MCP web search settings should be enabled if that runtime path is being evaluated;
- hidden holdout must live outside the repository.

The optimization corpus itself is repository data:

```text
data/kontur_all_783_queries.json
```

## Start a new run

From `codex/deepseek-query-interpreter`:

```bash
git pull origin codex/deepseek-query-interpreter
uv sync --extra test --extra review
python -m pytest -q

docker compose up -d qdrant
python scripts/restore_qdrant.py
```

Then launch the harness:

```bash
uv run python scripts/run_rag_harness.py \
  --holdout ~/rag-private/rag_hidden_holdout.json \
  --push
```

For a one-scientific-iteration smoke campaign:

```bash
uv run python scripts/run_rag_harness.py \
  --holdout ~/rag-private/rag_hidden_holdout.json \
  --push \
  --max-cycles 1
```

`python -m harness_rag ...` uses the same optimization runner.

## Resume after an interruption

Provider/Qdrant/current-dataset failures preserve the active stage. Restore the dependency and run:

```bash
uv run python scripts/run_rag_harness.py \
  --holdout ~/rag-private/rag_hidden_holdout.json \
  --push \
  --resume
```

Do not use `--fresh` for an infrastructure interruption.

## External state

Default location is outside the repository:

```text
~/.rag-trend-harness/<branch>/
```

Important artifacts include:

```text
state.json
history_v2.jsonl
events.jsonl
preflight.json
campaigns/<campaign-id>/runs/<attempt>/...
current_dataset/champion-<commit>.compact.json
current_dataset/champion-<commit>.summary.json
campaigns/<campaign-id>/runs/<attempt>/current_dataset/candidate.compact.json
campaigns/<campaign-id>/runs/<attempt>/current_dataset/candidate.summary.json
```

The huge raw 783 MCP/debug JSON is deleted after each harness evaluation. Compact per-row evidence and summaries are retained.

## Guardrails

- Do not optimize unlabeled `MATCHED` count directly.
- Missing catalog evidence is not a negative fact.
- Do not weaken technical compatibility merely to increase coverage.
- Do not hardcode GOLD ids, known answers, or one-off tender strings.
- Public/hidden regressions are rejected before the expensive 783 candidate run.
- Research should prefer recurring mechanisms over case-specific patches.
- Index builds are supervisor-owned and budgeted.
- Accepted experiments accumulate: every next cycle starts from the latest champion.
