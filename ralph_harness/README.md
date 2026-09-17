# Ralph Experiment Harness

Reusable, domain-agnostic autonomous experiment loop extracted from the `rag_trend` research harness.

It does **not** know about RAG, Qdrant, retrieval, DeepSeek, images, APIs, or any particular product. A target project supplies only:

- a goal (`RALPH_GOAL.md`);
- deterministic tests;
- one or more evaluator commands that write JSON;
- metric policies for hard gates.

The harness supplies the experiment lifecycle, agent roles, state/resume, champion management, rollback, experiment memory and promotion.

## Loop

```text
current champion
      |
      v
  RESEARCHER
      |
      v
    PLANNER -----> DONE
      |
      v
 IMPLEMENTER
      |
      v
    TESTS --------> rollback
      |
      v
 GATE EVALS ------> rollback
      |
      v
EVIDENCE EVALS
      |
      v
   REVIEWER ------> rollback
      |
    ACCEPT
      v
 commit/push
      |
      v
 new champion -> next experiment
```

Each accepted or rejected hypothesis is retained in external state as experiment memory:

```text
experiment_id
hypothesis
candidate deltas
decision
reason
lesson
next cycle
```

The next Researcher and Planner receive the previous experiment history, which turns a naive "keep asking an agent to improve the code" loop into a bounded research process.

## Why there are two evaluator types

### `gate`

Use for trusted labeled or deterministic checks: accuracy, hard-pass rate, error rate, latency SLO, unit benchmark, safety constraint, etc.

A candidate must pass its numeric policy before expensive evaluation continues.

### `evidence`

Use for large, expensive, weakly labeled or unlabeled corpora. These outputs are passed to the Reviewer as evidence but are **not** blindly optimized by a numeric threshold.

This is the generic equivalent of the pattern used in `rag_trend`:

```text
small trusted GOLD -> gate
large current 783-row corpus -> evidence
```

The same pattern works for other domains, for example:

```text
unit benchmark -> gate
real production traces -> evidence
```

or

```text
human preference holdout -> gate
10k generated samples -> evidence
```

## Evaluator contract

Every evaluator command receives an output path through the `{output}` placeholder and must write one JSON object there.

Minimal example:

```json
{
  "metrics": {
    "quality_score": 0.82,
    "error_rate": 0.01
  }
}
```

It may additionally return reviewer evidence:

```json
{
  "metrics": {
    "matched_rate": 0.41
  },
  "summary": {
    "failure_buckets": {
      "parse": 12,
      "retrieval": 31
    }
  },
  "changed_examples": [
    {
      "before": "...",
      "after": "..."
    }
  ]
}
```

Use `review_keys` in config to control which payload fields enter the Reviewer context.

## Gate policy

Example:

```json
{
  "name": "gold",
  "kind": "gate",
  "command": ["python", "scripts/evaluate.py", "--output", "{output}"],
  "policy": {
    "primary": {
      "metric": "quality_score",
      "direction": "maximize",
      "min_delta": 0.0
    },
    "guardrails": [
      {
        "metric": "error_rate",
        "direction": "minimize",
        "max_regression": 0.0
      }
    ]
  }
}
```

`min_delta: 0` means the primary metric may stay flat but may not regress. The final Reviewer still decides whether a flat candidate has enough real incremental value to promote.

## Files

```text
ralph_harness/
  pyproject.toml
  README.md
  ralph.config.example.json
  RALPH_GOAL.example.md
  src/ralph_harness/
    agent.py       # Codex adapter
    cli.py         # CLI
    engine.py      # resumable experiment state machine
    evaluator.py   # generic gate/evidence adapters and metric policy
    prompts.py     # domain-neutral role prompts
    runtime.py     # git, rollback, subprocess primitives
    schemas.py     # structured role contracts
  tests/
```

## Install

From this directory:

```powershell
uv pip install -e ".[test]"
```

Or install it from another repository using a path to this folder:

```powershell
uv pip install -e "C:\path\to\rag_trend\ralph_harness"
```

The only runtime dependency is Python itself; `pytest` is only required for this harness project's tests.

The agent backend currently uses the local `codex` CLI because that is the proven backend extracted from the original loop.

## Add to a project

Copy the examples to the target repository root:

```powershell
Copy-Item ralph.config.example.json ..\my-project\ralph.config.json
Copy-Item RALPH_GOAL.example.md ..\my-project\RALPH_GOAL.md
```

Then edit:

1. `RALPH_GOAL.md` — desired outcome and experiment constraints.
2. `test_command` — cheap deterministic regression tests.
3. `evaluators` — project-specific evaluation commands.
4. `protected_paths` — files/data the Implementer must never alter.
5. models/reasoning budgets if needed.

Your evaluator scripts are the **only domain-specific adapter** required.

## Run

Run from the target project's branch, not `main`/`master`:

```powershell
ralph-loop run --config ralph.config.json --project-root . --push
```

Without automatic push:

```powershell
ralph-loop run --config ralph.config.json --project-root .
```

If an agent/provider/evaluator/process dies in the middle of a stage:

```powershell
ralph-loop run --config ralph.config.json --project-root . --resume --push
```

To intentionally discard the old campaign state and establish a new baseline from current HEAD:

```powershell
ralph-loop run --config ralph.config.json --project-root . --fresh
```

## State and reproducibility

State is deliberately kept **outside** the target repository:

```text
~/.ralph-harness/<project>/<branch>/
```

It contains:

```text
state.json
baseline/
runs/001/
runs/002/
...
FINAL_REPORT.md
```

This prevents the agent from editing its own experimental memory and keeps candidate diffs clean.

An accepted candidate is committed as:

```text
ralph: cycle NN <experiment_name>
```

A rejected candidate is reset back to the current champion with `git reset --hard` and untracked candidate files are cleaned. Therefore **start with a clean worktree** and keep personal/uncommitted work out of the experiment branch.

## Safety against metric gaming

The harness uses several independent barriers:

1. goal/config paths are automatically protected;
2. additional paths can be protected in config;
3. Implementer cannot commit or push;
4. deterministic tests run before evaluation;
5. `gate` evaluators reject metric regressions before expensive evidence work;
6. `evidence` evaluators are not assumed to be correctness metrics;
7. a separate Reviewer sees the hypothesis, diff, champion/candidate evidence and deltas;
8. rejected candidates are rolled back;
9. lessons from rejected experiments are retained so the loop does not blindly repeat them.

For high-stakes or expensive systems, keep an additional sealed holdout outside the adaptive loop and evaluate it separately after the campaign.

## What was intentionally NOT copied from the RAG harness

The reusable core does not include:

- Qdrant collection/index lifecycle;
- RAG-specific failure taxonomy;
- query interpreter logic;
- MCP/web enrichment logic;
- tender datasets;
- RAG-specific metrics or acceptance thresholds;
- project-specific OpenSpec conventions.

Those belong in project evaluators/adapters, not in the experiment supervisor.

## Intended use

This skeleton is suitable for repeated autonomous optimization where you can define observable evidence, for example:

- RAG/retrieval quality;
- agent coding workflows;
- model/prompt experiments;
- data processing pipelines;
- generation quality/diversity;
- latency/cost optimization;
- parsers/classifiers;
- recommendation/ranking systems;
- backend performance;
- evaluation methodology itself.

The central idea is stable: **one falsifiable experiment at a time, compare against a champion, preserve evidence and lessons, rollback by default.**
