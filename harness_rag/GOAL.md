# Goal

Improve the accuracy and reliability of the current LD nomenclature-matching MVP through measured autonomous research and implementation.

Harness v2 runs a bounded campaign of at most **7 cycles**. Planning, bounded research and mechanism review use the strongest configured model. Product implementation and one-pass fixes use the cheaper configured model. The purpose of the harness is to spend strong-model calls on decisions and cheap-model calls on code, while deterministic checks eliminate weak candidates as early as possible.

## Primary objective

Maximize measured MVP quality. The Planner may change any product implementation component when evidence supports it: query processing, normalization, parsing, structured constraints, candidate generation, BM25/dense retrieval, fusion, reranking, DeepSeek usage, prompts, business logic, model choice, index representation, thresholds, or the overall matching architecture.

The current architecture is not sacred. The failure taxonomy and research references are evidence and idea sources, not a mandatory implementation order.

Do not optimize by hardcoding a public test id, exact GOLD answer, known product id, or one-off benchmark string. Prefer changes that plausibly improve a class of real tender inputs. Preserve uncertainty: **unknown catalog evidence is not a negative fact and must not silently become a default value**.

## Cycle contract

A cycle begins with the Planner choosing the highest-value uncertainty or bottleneck. It may choose one of three actions:

- `IMPLEMENT` — create one falsifiable product hypothesis and one fresh `v2-cycle-NN-*` OpenSpec change for the cheap Implementer.
- `RESEARCH` — resolve one narrow external/domain uncertainty before writing code. Research must be bounded, source-backed, and must not edit the repository.
- `DONE` — stop early when the accumulated evidence no longer supports a credible positive-value next step.

A research cycle is deliberately allowed to replace a coding cycle. Do not write code merely to consume the seven-cycle budget.

For implementation cycles, run cheap deterministic work before expensive judgment:

1. Implementer and focused tests.
2. Full tests and any supervisor-owned index rebuild.
3. Public evaluation and regression/safety gate.
4. Adaptive hidden-validation gate.
5. Strong read-only Reviewer only for candidates that already survived the metric gates.
6. At most one cheap Fixer pass when the Reviewer identifies a small correction that preserves the same hypothesis.
7. Re-run deterministic/public/hidden checks after a fix, then promote or roll back.

The Reviewer judges the candidate against the **current champion**, never against the historical baseline. A metric gain is not sufficient when the mechanism relies on a false or unsupported semantic premise.

## Memory and hypothesis selection

The Planner receives compact durable memory rather than raw transcripts or stack traces. Preserve:

- hypothesis family and falsifiable claim;
- whether the hypothesis was scientifically evaluated;
- public/hidden result and case-level delta;
- concise accept/reject reason and durable lesson;
- prior bounded research findings and sources;
- a persistent hypothesis ledger showing attempts, valid evaluations and diminishing returns;
- predicted case delta, confidence, risk and the mechanism success signal when available.

Infrastructure/protocol failures are not evidence against the scientific hypothesis. Repeating a failed scientific idea is justified only when new evidence materially changes it. Repeating an unevaluated idea may be appropriate after its external blocker is removed.

## Evaluation sets

Repository GOLD is public development feedback. Treat hard-gate/safety behavior as regression protection and difficult matching cases as capability feedback. `UNSCORED` diagnostics must remain separate from scored failures so they do not crowd out actionable planning evidence.

`--holdout` is **adaptive hidden validation**. Its rows, labels and per-case failures are never sent to Planner, Researcher, Implementer, Reviewer or Fixer; agents may receive only aggregate hidden-validation metrics.

An optional `--final-holdout` is a **sealed final set**. It must be outside the repository, distinct from adaptive hidden validation, and is evaluated at most once after the campaign has already reached the public/hidden success condition. No agent receives its contents or feedback.

## Success and guardrails

Success means:

- public and adaptive hidden hard-pass coverage reach at least 93%;
- a candidate never regresses public or hidden hard-pass coverage versus the current champion;
- false matches, explicit human-reject failures and wrong-NOT_FOUND safety failures do not regress;
- promoted changes have measured value and a technically credible mechanism;
- no more than five full index rebuilds are used across the campaign;
- the configured agent/research/reviewer/fixer call budgets are respected.

The 93% value is a target, not a requirement to burn all seven cycles. Prefer `DONE` over a weak, repetitive or poorly evidenced hypothesis.

## Infrastructure and interruption semantics

Environment health is part of the harness, not part of a scientific result. Before any LLM call, the supervisor verifies the public dataset, required credentials, Qdrant readiness, the active collection and external holdout paths.

If Qdrant, networking, an external model provider or another required dependency fails after a cycle has started, the harness must **pause the active stage rather than reject the hypothesis or advance to another cycle**. Preserve the candidate and completed stages, return a temporary-failure status, and continue the same stage with `--resume` after the dependency is restored.

Do not place raw infrastructure tracebacks into Planner memory. Keep full diagnostics in run logs and expose only a typed error code plus a concise reason to subsequent planning.
