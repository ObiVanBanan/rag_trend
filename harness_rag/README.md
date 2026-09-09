# RAG research harness

This branch contains a bounded autonomous loop for improving `rag_trend` without turning the GOLD set into something the coding agent can simply memorize.

## Idea in one screen

1. **Baseline** — measure the current solution once.
2. **Planner** — GPT-5.6 Terra / medium analyzes the current champion, failure taxonomy, research context, and previous hypotheses. It uses the installed `openspec-propose` skill and creates one bounded OpenSpec change.
3. **Implementer** — GPT-5.5 / low applies only that change.
4. **Public eval** — tests plus the repository GOLD give detailed feedback for research.
5. **Reviewer** — GPT-5.6 Terra / medium is read-only. It reads code, diff and public metrics and returns ACCEPT / FIX / REJECT.
6. **Fixer** — GPT-5.5 / low applies only the review plan.
7. **End hook** — run the public eval again and then the blind holdout. Hidden rows are never sent to any agent; only aggregate metrics survive.
8. **Champion** — keep the candidate only if blind coverage did not fall, safety did not regress, and either public or blind quality improved. Otherwise reset to the previous champion.
9. Repeat for at most **15 cycles**.

Full index rebuilds are requested by agents but executed only by the supervisor. The global research budget is **5 rebuilds**.

## Why two checks

The existing GOLD/tender cases are already in the repository, so they are useful as a **public development set**, not a truly blind test. A final check that agents cannot inspect must be a separate harness dataset stored outside the repository.

The blind check has a hard success condition of **>= 93% hard-pass coverage**, while false matches and explicit human-reject failures must remain zero. During research the champion is monotonic: a new candidate may not reduce blind coverage versus the current champion.

> Important: keeping the holdout outside the repository prevents ordinary accidental leakage, but it is not a cryptographic sandbox boundary. For hostile-agent-grade isolation, run Codex in a container/OS user that cannot read the holdout and let only the outer evaluator access it.

## Index isolation

A hypothesis that changes index-time representation must not overwrite the current champion's Qdrant collection before it is accepted. The intended harness behavior is to rebuild into a fresh temporary Qdrant collection, evaluate against it, and remember that collection only if the code becomes the new champion. Rejected experimental collections can be deleted later.

After research finishes, if the champion uses a temporary harness collection, rebuild/promote the selected champion to the normal production Qdrant alias. That deployment rebuild is separate from the five experimental rebuilds.

## Run

Use a dedicated clone/worktree and the dedicated branch:

```bash
git fetch origin
git switch codex/rag-harness-rnd
git pull
python -m pytest -q tests/test_rag_harness_policy.py tests/test_tender_unresolved_taxonomy.py
```

Prepare a blind dataset in the same harness JSON format **outside this repository**, then:

```bash
python scripts/run_rag_harness.py \
  --holdout /absolute/path/outside/repo/rag_hidden_holdout.json \
  --fresh
```

To push accepted champion commits automatically:

```bash
python scripts/run_rag_harness.py \
  --holdout /absolute/path/outside/repo/rag_hidden_holdout.json \
  --push \
  --fresh
```

State and complete run history are stored outside the repo by default under:

```text
~/.rag-trend-harness/codex__rag-harness-rnd/
```

The important artifacts are:

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

## Model aliases

`config.json` contains the requested defaults:

- Planner: `gpt-5.6-terra`, reasoning `medium`
- Reviewer: `gpt-5.6-terra`, reasoning `medium`
- Implementer: `gpt-5.5`, reasoning `low`
- Fixer: `gpt-5.5`, reasoning `low`

If the local Codex installation exposes different exact aliases, override them at launch:

```bash
python scripts/run_rag_harness.py \
  --holdout /path/to/holdout.json \
  --planner-model <local-model-alias> \
  --reviewer-model <local-model-alias> \
  --worker-model <local-model-alias> \
  --fixer-model <local-model-alias>
```

## What the Planner should try first

Read `RESEARCH_CONTEXT.md`. The current evidence says the main unresolved groups are alias/designation mapping and missing catalog/schema evidence, not proven dense-retrieval misses. So the loop should normally investigate:

```text
aliases/designations
    -> parser/schema
    -> catalog/index representation
    -> hybrid retrieval / fusion
    -> reranking / query expansion
    -> embeddings or fine-tuning only later
```

The Planner is free to reject that order when the measured evidence supports another bounded hypothesis.
