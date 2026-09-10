# RAG research Harness v2

This branch contains the second version of the autonomous research harness for `rag_trend`. Its main design constraint is resource efficiency: **strong models decide what to do; cheap models write and repair code; deterministic gates prevent spending strong-model calls on obviously bad candidates.**

## Main changes from v1

Harness v2 runs at most **7 campaign cycles** instead of 15 and adds several protections learned from the first 17-cycle campaign:

- deterministic preflight before any LLM call;
- Qdrant/provider failures pause the current stage instead of consuming more cycles;
- stage-level resume preserves the active candidate and does not repeat completed agent calls;
- compact experiment memory plus a persistent hypothesis-family ledger instead of raw history/tracebacks;
- public `FAIL_*` cases are separated from `UNSCORED` diagnostics;
- Planner predictions are expressed in hard-gate cases with confidence/risk and an observable mechanism signal;
- bounded `RESEARCH` is a first-class Planner action when a domain premise cannot be justified from repository evidence;
- public and adaptive hidden metric gates run before the expensive Reviewer;
- Reviewer sees the **current champion, candidate and delta vs champion**, not just the historical baseline;
- Reviewer is conditional and the cheap Fixer gets at most one pass;
- hidden validation is explicitly adaptive selection feedback; an optional sealed final holdout is separate;
- usage, stage transitions and typed failure codes are persisted outside the repository.

The old 1-17 campaign remains useful research memory. On first v2 run, an existing v1/v2-old `state.json` is migrated automatically, backed up as `state.v1-backup.json`, and a fresh seven-cycle campaign starts from the saved champion rather than discarding it.

## Models and budgets

Defaults are in `harness_rag/config.json`:

```text
Planner / bounded Research: gpt-5.6-terra, medium
Reviewer:                   gpt-5.6-terra, medium
Implementer:                gpt-5.5, low
Fixer:                      gpt-5.5, low

Campaign cycles:            7
Total agent calls:          18
Research calls:             2
Reviewer calls:             3
Fixer calls:                2
Full index rebuilds:        5 global maximum
```

The cycle limit controls research breadth; the separate call budgets prevent a cycle with review/fix/research from silently exploding model usage.

## Execution flow

```text
PRE-FLIGHT (no LLM)
  ├─ public dataset exists
  ├─ required credentials exist
  ├─ hidden/final paths are outside repo
  └─ Qdrant + active collection are healthy
          │
          ▼
PLANNER — strong model
  ├─ DONE
  ├─ RESEARCH ──► bounded strong-model research (<=3 reported sources)
  └─ IMPLEMENT
          │
          ▼
IMPLEMENTER — cheap model
          │
          ▼
FULL TESTS / OPTIONAL INDEX BUILD
          │
          ▼
PUBLIC EVAL
  ├─ regression/safety failure ──► rollback immediately
  └─ survives
          │
          ▼
ADAPTIVE HIDDEN VALIDATION
  ├─ no safe improvement ──► rollback
  └─ survives
          │
          ▼
REVIEWER — strong, read-only, conditional
  ├─ REJECT ──► rollback
  ├─ ACCEPT ──► promote
  └─ FIX ──► cheap Fixer once
                 │
                 ▼
          tests + public + hidden
                 │
                 ▼
              promote/rollback
```

This ordering is intentional. A public regression no longer spends a hidden-validation run or Reviewer call, and a candidate that cannot beat the hidden champion never reaches the strong Reviewer.

## Research action

The Planner may return `RESEARCH` instead of forcing an implementation when the next decision depends on a missing external/domain fact. Research is narrow and evidence-oriented:

- at most two research calls per seven-cycle campaign by default;
- at most three reported external sources per call;
- preference for standards, manufacturer documentation, papers and authoritative technical sources;
- source URL, claim, source quality and confidence are retained in durable research memory;
- research cannot edit product/OpenSpec files or inspect hidden data.

The source-count rule is enforced by the structured output schema and prompt; the **number of research model calls** is hard-enforced by the supervisor budget.

## Public, hidden validation and sealed final

Repository GOLD is public development feedback. `FAIL_*` rows are sent to planning as scored failures; `UNSCORED` rows are represented only by their count so they cannot crowd out real failures.

`--holdout` is adaptive hidden validation. Detailed rows are deleted immediately after evaluation; only aggregate metrics remain in state. Repeated promotion decisions may use this aggregate score, so it is intentionally not called a final holdout anymore.

`--final-holdout` is optional. If supplied, it must be outside the repository and different from `--holdout`. It is evaluated at most once, only after public + hidden validation have already reached the configured goal. Its contents are never shown to an agent.

## Infrastructure failures and resume

Infrastructure failures do **not** become scientific rejections. If Qdrant disappears during public/hidden evaluation or an index build, Harness v2 writes the full diagnostics to the run directory, stores a concise typed error in state, leaves the candidate intact and exits with temporary-failure code `75`.

After restoring the dependency:

```bash
python scripts/run_rag_harness.py \
  --holdout ~/rag-private/rag_hidden_holdout.json \
  --push \
  --resume
```

The same cycle resumes at the saved stage. Do not use `--fresh` for an infrastructure/provider interruption.

Preflight also catches a missing Qdrant before the first Planner call, so the failure mode that consumed cycles 11-15 in the v1 campaign should now cost zero LLM calls.

## State and observability

Default state location:

```text
~/.rag-trend-harness/codex__rag-harness-rnd/
```

Important files:

```text
state.json                 current champion, active stage, ledger and budgets
state.v1-backup.json       automatic backup when old state is migrated
events.jsonl               append-only stage/event audit trail
history_v2.jsonl           compact completed-cycle history
preflight.json             most recent environment health check
runs/001/plan.json
runs/001/research.json     only for RESEARCH action
runs/001/worker.json
runs/001/candidate.diff
runs/001/review.json       only if candidate reaches Reviewer
runs/001/*_error.log       full infrastructure/evaluator diagnostics
FINAL_REPORT_V2.md
```

`state.json` keeps concise failure codes/reasons; long tracebacks stay in logs and are not copied back into Planner context.

The usage ledger records total calls, calls by role/model, elapsed agent time and token counts when the installed Codex CLI exposes a parseable token total in its log.

## First run after upgrading from the old campaign

Keep Qdrant running and keep the old external state if you want v2 to learn from cycles 1-17.

```bash
git fetch origin
git switch codex/rag-harness-rnd
git pull

docker compose up -d qdrant
curl -fsS http://localhost:6333/readyz

python -m pytest -q
```

For a cheap smoke campaign, run exactly one v2 cycle first:

```bash
unset RAG_HOLDOUT_KEY

python scripts/run_rag_harness.py \
  --holdout ~/rag-private/rag_hidden_holdout.json \
  --push \
  --max-cycles 1
```

On the first invocation, old state is migrated automatically and the v2 campaign counter starts at cycle 1 while preserving the current champion and compact lessons from the old campaign.

If that cycle completes cleanly, continue the same campaign with the normal seven-cycle ceiling:

```bash
python scripts/run_rag_harness.py \
  --holdout ~/rag-private/rag_hidden_holdout.json \
  --push
```

Because the completed v2 cycle is already in state, this continues at cycle 2 rather than starting over.

Use `--new-campaign` only after a v2 campaign is complete and you intentionally want another seven-cycle research budget while carrying its durable memory forward. Use `--fresh` only when you deliberately want to discard external harness state and establish a new baseline.

## Guardrails

- Never hardcode public benchmark ids, GOLD product ids or hidden answers.
- Planner writes one fresh `v2-cycle-NN-*` OpenSpec change for IMPLEMENT actions; product code remains the Implementer's responsibility.
- Product architecture may change, but harness policy, GOLD/evaluator truth, hidden plumbing and catalog source data are protected.
- `unknown` is not `negative`; absent catalog metadata must not become an invented default fact.
- Full index rebuilds are supervisor-owned and budgeted.
- Git push is publication, not an acceptance gate: a local accepted champion is kept if GitHub auth temporarily fails.
