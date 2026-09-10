# Research context

This file is an evidence and idea library for the Planner/Researcher. It is **not** a prescribed solution order. Harness v2 loads this file just-in-time instead of copying it into every Planner prompt.

## Current domain evidence

The unresolved tender taxonomy has 24 cases: 12 `CATALOG_NO_MATCH`, 7 `ALIAS_MAPPING_MISS`, 3 `SCHEMA_GAP`, 1 `PARSER_MISS`, 1 `QUERY_UNDERSPECIFIED`, and 0 proven `RETRIEVAL_MISS` cases.

Treat these labels as observations, not conclusions about what must be implemented next. For per-case evidence read `data/tender_queries_v1_unresolved_taxonomy.json` only when the selected hypothesis requires it.

## Durable lessons from the first harness campaign

These are mechanism-level lessons, not a ban on revisiting a family with materially new evidence:

- additive, semantics-preserving query canonicalization improved both public and hidden-validation coverage and is the strongest demonstrated mechanism so far;
- hard candidate exclusion based on partially inferred catalog facts regressed recall/safety; `unknown` must not be treated as `negative` or as an invented default;
- reranker prompt tightening alone did not produce a reliable safe gain in the tested form;
- heuristic catalog/index enrichment can regress hidden coverage when the derived labels are not trustworthy;
- coarse candidate diversification removed technically meaningful variants and regressed public coverage when validly tested;
- `Управление` is not a sufficiently reliable standalone truth source for distinguishing installed electric actuation from actuator-ready ball valves; a metric gain on that premise was correctly rejected by mechanism review;
- flange-subtype and standards-backed outer-diameter-to-DN hypotheses were not scientifically resolved in the old campaign because their evaluation attempts were interrupted by protocol/infrastructure failures.

Infrastructure and protocol failures are not negative evidence about the underlying hypothesis.

## Solution space

The Planner is free to investigate any of these families, in any order, or combine them when one bounded experiment can test the mechanism cleanly:

- query normalization and typo/mixed-script handling;
- structured query extraction and constraint-aware matching;
- alias/designation understanding;
- candidate generation and pre-filtering;
- BM25, dense retrieval, hybrid fusion, candidate depth and thresholds;
- reranker prompts, features, model choice and decision logic;
- DeepSeek-based query interpretation, expansion or candidate verification;
- catalog/search-text enrichment and index representation;
- deterministic post-retrieval validation;
- pipeline or architecture changes that replace weak parts of the current MVP;
- calibration, confidence, NOT_FOUND behavior and ambiguity handling;
- embedding/model changes or fine-tuning when supported by evidence.

Prefer the direction with the best expected information or metric gain for the remaining model/cycle budget. A useful implementation hypothesis should address a class of inputs even if one public case exposed the issue.

## Experiment selection

Before choosing an IMPLEMENT action, compare materially different families against:

- likely hard-gate cases fixed, stated as a case count;
- risk of false matches, human-reject or wrong-NOT_FOUND regressions;
- whether the causal/mechanism premise is supported by repository evidence;
- whether external research is required before the premise is safe to encode;
- implementation and index-build cost;
- the persistent hypothesis ledger and diminishing returns from previous attempts.

If an important premise cannot be established from repository/catalog evidence, use a bounded RESEARCH action rather than writing a speculative rule.

## Harness design references

### SWE-agent — Agent-Computer Interfaces Enable Automated Software Engineering
https://arxiv.org/abs/2405.15793

Useful lesson: agent performance depends heavily on the interface and feedback loop, not only the model. Give agents explicit metrics, reproducible execution and bounded actions.

### Reflexion — Language Agents with Verbal Reinforcement Learning
https://arxiv.org/abs/2303.11366

Useful lesson: preserve compact episodic memory of attempts, results and lessons instead of replaying raw trajectories.

### Self-Refine — Iterative Refinement with Self-Feedback
https://arxiv.org/abs/2303.17651

Useful lesson: separate implementation from critique/correction when the extra critique call is justified. Harness v2 therefore makes Reviewer/Fixer conditional rather than mandatory for every candidate.

### OpenHands — An Open Platform for AI Software Developers as Generalist Agents
https://arxiv.org/abs/2407.16741

Useful lesson: sandboxing, evaluation benchmarks, explicit roles and reproducible traces are first-class parts of an autonomous coding system.

### DSPy — Compiling Declarative Language Model Calls into Self-Improving Pipelines
https://arxiv.org/abs/2310.03714

Useful lesson: optimize against explicit measurements. Retrieval, prompts, reranking and model calls are replaceable components rather than sacred architecture.

## Retrieval references

### BEIR — A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models
https://arxiv.org/abs/2104.08663

Useful lesson: BM25 remains a strong baseline; dense retrieval is not automatically superior out of domain; reranking helps only when candidate recall is adequate.

### Reciprocal Rank Fusion
https://doi.org/10.1145/1571941.1572114

Useful lesson: simple rank fusion can robustly combine heterogeneous retrievers, but it remains an experimental choice.

### HyDE — Precise Zero-Shot Dense Retrieval without Relevance Labels
https://aclanthology.org/2023.acl-long.99/

Useful lesson: generated query/document expansion can improve zero-shot retrieval, but hallucinated engineering properties are a risk and must not become deterministic truth without evidence.

## Adaptive validation and sealed final

### The Reusable Holdout / adaptive data analysis
https://doi.org/10.1126/science.aaa9375
https://arxiv.org/abs/1506.02629

Repeated adaptation to one test set can overfit the test distribution even when individual hidden rows are not shown. Harness v2 therefore distinguishes:

- public GOLD/failure taxonomy — detailed development feedback;
- adaptive hidden validation (`--holdout`) — rows remain secret, aggregate metrics may guide champion selection;
- optional sealed final (`--final-holdout`) — a distinct external dataset evaluated at most once after the goal is already met.

Agents never receive hidden/final rows, labels, product ids or per-case failures.
