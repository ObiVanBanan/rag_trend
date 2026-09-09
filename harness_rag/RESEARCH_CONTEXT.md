# Research context

This file narrows the search space for the Planner. It is not a prescription: every cycle still needs a bounded falsifiable hypothesis and evidence.

## What the current evidence says

The unresolved tender taxonomy currently has 24 cases: 12 `CATALOG_NO_MATCH`, 7 `ALIAS_MAPPING_MISS`, 3 `SCHEMA_GAP`, 1 `PARSER_MISS`, 1 `QUERY_UNDERSPECIFIED`, and 0 proven `RETRIEVAL_MISS` cases.

That means the Planner should not start by blindly changing embeddings. A useful default order is:

1. **Alias/designation normalization** — old/vendor designations such as `30с41нж`, `11с39п`, etc. should be translated into explicit product-family semantics before ranking.
2. **Parser and deterministic schema** — represent requirements humans can see but the matcher currently cannot judge safely: flange subtype, pipe outside diameter vs DN, temperature range, kit contents, construction/execution.
3. **Catalog representation** — improve the text/fields that are indexed so exact engineering attributes and aliases are present in searchable form.
4. **Hybrid retrieval/fusion** — only after a known-valid target exists, test BM25/dense/RRF parameters, query expansion, candidate depth, and reranking.
5. **Model/fine-tuning changes** — later, after enough trusted labels exist to tell whether the model is actually improving rather than memorizing examples.

## Harness design references

### SWE-agent — Agent-Computer Interfaces Enable Automated Software Engineering
https://arxiv.org/abs/2405.15793

Useful lesson: agent performance depends heavily on the interface and feedback loop, not only the model. Give agents a constrained workspace, explicit tests, and bounded actions rather than an unconstrained long prompt.

### Reflexion — Language Agents with Verbal Reinforcement Learning
https://arxiv.org/abs/2303.11366

Useful lesson: preserve compact episodic memory of previous attempts, results, and lessons so the Planner does not repeat failed hypotheses. Our run history is the equivalent of the reflective memory buffer.

### Self-Refine — Iterative Refinement with Self-Feedback
https://arxiv.org/abs/2303.17651

Useful lesson: separate initial implementation from critique and correction. This motivates Implementer -> read-only Reviewer -> Fixer rather than letting one agent endlessly edit its own code.

### OpenHands — An Open Platform for AI Software Developers as Generalist Agents
https://arxiv.org/abs/2407.16741

Useful lesson: sandboxing, evaluation benchmarks, explicit agent roles, and reproducible execution traces are first-class parts of the system.

### DSPy — Compiling Declarative Language Model Calls into Self-Improving Pipelines
https://arxiv.org/abs/2310.03714

Useful lesson: optimize against an explicit metric, but keep the metric outside the implementation agent. Treat prompts/retrieval/reranking as pipeline components that can be changed experimentally rather than as sacred architecture.

## Retrieval references

### BEIR — A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models
https://arxiv.org/abs/2104.08663

Useful lesson: BM25 remains a robust baseline; dense retrieval is not automatically superior out of domain; reranking can be strong but expensive. Preserve lexical retrieval as a serious component.

### Reciprocal Rank Fusion
https://doi.org/10.1145/1571941.1572114

Useful lesson: simple rank fusion can robustly combine heterogeneous retrievers. Tune or replace RRF only with evidence from known-positive cases.

### HyDE — Precise Zero-Shot Dense Retrieval without Relevance Labels
https://aclanthology.org/2023.acl-long.99/

Useful lesson: generated query/document expansion can improve zero-shot retrieval, but generated text may hallucinate. In this project it is a later hypothesis, not the first fix, because exact DN/PN/material/execution constraints must remain deterministic.

## Why the final check is blind

### The Reusable Holdout / adaptive data analysis
https://doi.org/10.1126/science.aaa9375
https://arxiv.org/abs/1506.02629

Repeatedly adapting a system to a test set can overfit the test set itself. Therefore:

- public GOLD and failure taxonomy are research/development feedback;
- the final holdout is stored outside the repo;
- agents never receive hidden rows, labels, product ids, or per-case failures;
- the supervisor exposes only aggregate coverage/safety metrics;
- the current champion may never regress on the blind metric;
- final success requires blind coverage >= 93%.

## Hypothesis menu

Prefer one hypothesis per cycle. Examples, roughly in priority order:

- explicit designation alias table + deterministic application before retrieval;
- distinguish pipe OD notation from DN in parsing;
- add missing structured fields and corresponding candidate checks;
- enrich indexed product text with canonical attributes and normalized aliases;
- improve query normalization while preserving exact engineering numbers;
- adjust lexical/dense candidate depths and RRF only on known-positive retrieval failures;
- reranker prompt/features for close candidates after recall is adequate;
- LLM query expansion/HyDE only if simpler normalization/fusion hypotheses fail;
- embedding-family changes or fine-tuning only after the above and only with reproducible gains.

Avoid hypotheses whose only justification is "try a newer model" or "increase top-k" without a diagnosed failure mode.
