# Research context

This file is an evidence and idea library for the Planner. It is **not** a prescribed solution order and must not prevent the Planner from trying a materially different architecture when the metrics justify it.

## Current evidence

The unresolved tender taxonomy currently has 24 cases: 12 `CATALOG_NO_MATCH`, 7 `ALIAS_MAPPING_MISS`, 3 `SCHEMA_GAP`, 1 `PARSER_MISS`, 1 `QUERY_UNDERSPECIFIED`, and 0 proven `RETRIEVAL_MISS` cases.

Treat these labels as observations, not conclusions about what must be implemented next. Public failures and the complete experiment history should determine the next hypothesis.

## Solution space

The Planner is free to investigate any of these families, in any order, or combine them when one bounded experiment can test the idea cleanly:

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

Do not avoid a potentially high-value idea merely because it appears later in this list. Conversely, do not change a component only because it is fashionable. Prefer the experiment with the best expected information or metric gain per cycle.

A useful hypothesis should generally address a **class of inputs**, even when one public case exposed the issue. For example, a known case may reveal weak control-type handling, but the proposed change should improve control semantics generally rather than special-case that test id.

## Experiment selection

Before choosing a change, compare several materially different hypotheses against:

- expected improvement in public/blind hard-pass coverage;
- risk of false matches or human-reject regressions;
- whether the failure mechanism is supported by repository evidence;
- implementation cost and remaining cycle/index budget;
- what previous experiments already taught us.

The complete history of earlier attempts is first-class research evidence. Do not silently repeat an already rejected hypothesis. If revisiting one, state what new evidence makes the revised experiment different.

## Harness design references

### SWE-agent — Agent-Computer Interfaces Enable Automated Software Engineering
https://arxiv.org/abs/2405.15793

Useful lesson: agent performance depends heavily on the interface and feedback loop, not only the model. Give agents explicit metrics, reproducible execution and bounded actions.

### Reflexion — Language Agents with Verbal Reinforcement Learning
https://arxiv.org/abs/2303.11366

Useful lesson: preserve episodic memory of previous attempts, results and lessons so future planning is conditioned on evidence rather than starting over.

### Self-Refine — Iterative Refinement with Self-Feedback
https://arxiv.org/abs/2303.17651

Useful lesson: separate implementation from critique and correction. This motivates Implementer -> read-only Reviewer -> Fixer.

### OpenHands — An Open Platform for AI Software Developers as Generalist Agents
https://arxiv.org/abs/2407.16741

Useful lesson: sandboxing, evaluation benchmarks, explicit roles and reproducible traces are first-class parts of an autonomous coding system.

### DSPy — Compiling Declarative Language Model Calls into Self-Improving Pipelines
https://arxiv.org/abs/2310.03714

Useful lesson: optimize the pipeline against explicit metrics. Retrieval, prompts, reranking and model calls are all replaceable components rather than fixed architecture.

## Retrieval references

### BEIR — A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models
https://arxiv.org/abs/2104.08663

Useful lesson: BM25 remains a strong baseline; dense retrieval is not automatically superior out of domain; reranking can help when candidate recall is adequate.

### Reciprocal Rank Fusion
https://doi.org/10.1145/1571941.1572114

Useful lesson: simple rank fusion can robustly combine heterogeneous retrievers, but it is still an experimental choice and can be tuned or replaced when evidence supports it.

### HyDE — Precise Zero-Shot Dense Retrieval without Relevance Labels
https://aclanthology.org/2023.acl-long.99/

Useful lesson: generated query/document expansion can improve zero-shot retrieval, but hallucinated engineering properties are a risk and must be checked against deterministic constraints.

## Why the final check is blind

### The Reusable Holdout / adaptive data analysis
https://doi.org/10.1126/science.aaa9375
https://arxiv.org/abs/1506.02629

Repeatedly adapting a system to a test set can overfit the test set itself. Therefore:

- public GOLD and failure taxonomy are development feedback;
- the final holdout is stored outside the repo;
- agents never receive hidden rows, labels, product ids, or per-case failures;
- the supervisor exposes only aggregate blind metrics;
- the current champion may never regress on the blind metric;
- final success requires blind coverage >= 93%.
