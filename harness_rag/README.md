# RAG research harness

This branch contains a bounded autonomous loop for improving `rag_trend` while protecting the blind final check from direct agent access.

## Objective

Improve the accuracy and reliability of the **current MVP**. The Planner is free to change any product implementation component when the evidence supports it: parsing, normalization, retrieval, reranking, DeepSeek usage, prompts, model choice, index representation, constraints, thresholds, or the overall matching pipeline.

The existing architecture is not a constraint. The failure taxonomy and research references are evidence, not a mandatory solution order.

The main success condition is blind hard-pass coverage **>= 93%**, with no blind coverage regression versus the current champion and no safety regression.

## Loop

1. **Baseline** — measure the current solution.
2. **Planner** — GPT-5.6 Terra / medium reads the repository, current metrics, public failures and the complete experiment history. It considers at least three materially different hypotheses and selects one.
3. **OpenSpec** — the Planner creates one fresh `cycle-NN-*` OpenSpec change. Previous accepted cycle specs cannot be overwritten.
4. **Implementer** — GPT-5.5 / low implements the selected hypothesis.
5. **Public eval** — tests plus repository GOLD provide detailed development feedback.
6. **Reviewer** — GPT-5.6 Terra / medium is read-only and returns ACCEPT / FIX / REJECT.
7. **Fixer** — GPT-5.5 / low applies only the review plan when needed.
8. **End hook** — public eval and then blind eval. Hidden rows are never sent to agents; only aggregate blind metrics survive.
9. **Champion** — keep a candidate only when it improves measured quality without the protected regressions; otherwise reset to the prior champion.
10. Repeat for at most **15 cycles**.

Full index rebuilds are executed only by the supervisor. Global experimental budget: **5 rebuilds**.

## Research memory

Every cycle is recorded outside the repository. The next Planner receives the complete history (up to all 15 cycles), including:

- hypothesis and plan summary;
- alternative hypotheses considered;
- expected effect / expected metric gain;
- accept/reject reason;
- public and blind hard-pass results and deltas from baseline;
- champion metric snapshots after each decision;
- index-build usage.

This is intended to prevent repeated failed experiments and make the loop cumulative rather than restarting its reasoning every cycle.

Default state location:

```text
~/.rag-trend-harness/codex__rag-harness-rnd/
```

Important artifacts:

```text
state.json
history.jsonl
runs/001/plan.json
runs/001/worker.json
runs/001/review.json
runs/001/gate.json
runs/001/candidate.diff
FINAL_REPORT.md
```

## Public vs blind check

Repository GOLD is development feedback. The blind holdout lives outside the repository and contains unseen tender-style wording. Agents do not receive hidden query text, labels, product ids or per-case failures.

The first blind set has:

```text
30 total
25 CORE
5 NEGATIVE
30 hard-gate
```

The measured starting point for the first run was:

```text
Public hard-pass: 24/30 = 80.0%
Blind hard-pass:  23/30 = 76.7%
Target:           >= 28/30 = 93.3%
```

## Run

Use the dedicated branch and a clean worktree:

```bash
git fetch origin
git switch codex/rag-harness-rnd
git pull

python -m pytest -q \
  tests/test_rag_harness_policy.py \
  tests/test_rag_harness_blind.py \
  tests/test_rag_harness_planner_memory.py \
  tests/test_tender_unresolved_taxonomy.py
```

Run a fresh research campaign after changing the harness contract:

```bash
unset RAG_HOLDOUT_KEY

python scripts/run_rag_harness.py \
  --holdout ~/rag-private/rag_hidden_holdout.json \
  --push \
  --fresh
```

Resume an interrupted campaign without `--fresh` only when the harness code and objective have not changed since that campaign began.

## Models

Defaults in `config.json`:

- Planner: `gpt-5.6-terra`, reasoning `medium`
- Reviewer: `gpt-5.6-terra`, reasoning `medium`
- Implementer: `gpt-5.5`, reasoning `low`
- Fixer: `gpt-5.5`, reasoning `low`

The exact aliases can be overridden on the CLI if the local Codex installation exposes different names.

## Guardrails

- Do not hardcode benchmark ids, GOLD product ids or exact blind answers.
- Product architecture may change; harness policy, GOLD/eval answers and blind plumbing may not.
- A single public failure may motivate an experiment, but the change should plausibly improve a class of real tender inputs.
- A five-build index budget is a resource constraint, not a reason to avoid a promising indexed-representation experiment.
