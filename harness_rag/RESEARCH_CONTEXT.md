# Research context

This file is a compact evidence/idea library for the research-first Harness v2. It is not a prescribed solution order.

## Current real-tender evidence

A new five-file Kontur sample expands the working distribution beyond the small hand-checked benchmark:

- `data/tender_queries_kontur_5files.json.gz` — 253 unique tender nomenclature rows extracted from 554 non-empty source rows after exact duplicate removal;
- `data/tender_queries_kontur_5files_labels_gold_v1.json.gz` — 90 conservative first-pass labels: 72 `MATCHED`, 18 `NOT_FOUND`;
- `data/tender_queries_kontur_5files_review_pool.json.gz` — 130 cases intentionally left unresolved/reviewable;
- `data/tender_queries_kontur_5files_provisional_not_found.json.gz` — 33 likely `NOT_FOUND` cases where the current retrieval did not surface the requested product family, but full-catalog evidence is not yet strong enough to call them final GOLD.

These files are gzip-compressed JSON. Inspect them selectively with Python `gzip`/`json` or `gzip -dc`; do not dump the entire corpus into an LLM prompt.

The 90 first-pass labels are useful evidence, not exhaustive truth. In particular, a returned candidate can be high-confidence compatible without proving that no better LD variant exists elsewhere in the full catalog. The 130 + 33 unresolved cases are the main discovery pool for new capabilities.

The existing `data/harness_gold_combined.json` and external hidden validation remain regression guardrails. Do not use their answer ids as the primary source of hypotheses.

## Legacy failure evidence

The earlier unresolved tender taxonomy contained 24 cases: 12 `CATALOG_NO_MATCH`, 7 `ALIAS_MAPPING_MISS`, 3 `SCHEMA_GAP`, 1 `PARSER_MISS`, 1 `QUERY_UNDERSPECIFIED`, and 0 proven `RETRIEVAL_MISS` cases. Treat that taxonomy as historical evidence from a much smaller sample, not as a complete description of the current real-tender distribution.

## Durable lessons from the first campaign

- additive, semantics-preserving query canonicalization improved both public and hidden-validation coverage and is the strongest demonstrated mechanism so far;
- hard candidate exclusion based on partially inferred catalog facts regressed recall/safety; `unknown` must not be treated as `negative` or as an invented default;
- reranker prompt tightening alone did not produce a reliable safe gain in the tested form;
- heuristic catalog/index enrichment can regress hidden coverage when derived labels are not trustworthy;
- coarse candidate diversification removed technically meaningful variants and regressed public coverage when validly tested;
- `Управление` is not a sufficiently reliable standalone truth source for distinguishing installed electric actuation from actuator-ready ball valves;
- infrastructure/protocol failures are not negative evidence about the underlying hypothesis.

## Open solution space

Research should compare materially different families before recommending a direction. Examples include:

- query normalization, typo and mixed-script handling;
- structured query extraction and constraint-aware matching;
- alias, manufacturer, model and designation understanding;
- candidate generation, BM25, dense retrieval, hybrid fusion, candidate depth and thresholds;
- reranker features, prompts, model choice and decision logic;
- DeepSeek query interpretation, expansion or candidate verification;
- catalog/search-text enrichment and index representation;
- deterministic post-retrieval validation;
- confidence, ambiguity and `NOT_FOUND` policy;
- embedding/model changes or fine-tuning when supported by evidence;
- pipeline or architecture changes that replace weak MVP components;
- **runtime external product enrichment**: when an input contains a manufacturer/model/designation but lacks decisive technical properties, search authoritative public sources for that exact product, extract only source-supported characteristics, then enrich the local LD search request.

Runtime web enrichment is deliberately listed as a possibility because it is a materially different architecture family. It is not a required answer. Research should prefer it only when real cases show that missing input knowledge, rather than local retrieval or catalog representation, is the bottleneck. Any external properties must preserve provenance/uncertainty and must not become invented deterministic facts.

## Research selection

A useful research pass should answer: **what class of real tender inputs currently limits useful matching, and what bounded experiment has the best information/value per model call?**

Prefer evidence such as recurring query structures, recurring current-pipeline failure modes, insufficient query information, missing catalog families, incompatible final selections, and a mechanism that can be checked after implementation. Do not optimize a handful of benchmark ids.

External research is optional. Use it when repository/catalog evidence cannot settle an important premise, and keep it bounded to at most three relevant sources.

## Harness references

### SWE-agent — Agent-Computer Interfaces Enable Automated Software Engineering
https://arxiv.org/abs/2405.15793

Useful lesson: agent performance depends heavily on the interface and feedback loop, not only the model. Give agents explicit feedback, reproducible execution and bounded actions.

### Reflexion — Language Agents with Verbal Reinforcement Learning
https://arxiv.org/abs/2303.11366

Useful lesson: preserve compact episodic memory of attempts, results and lessons instead of replaying raw trajectories.

### DSPy — Compiling Declarative Language Model Calls into Self-Improving Pipelines
https://arxiv.org/abs/2310.03714

Useful lesson: optimize replaceable pipeline components against explicit measurements rather than treating one architecture as sacred.

### BEIR — A Heterogeneous Benchmark for Zero-shot Evaluation of Information Retrieval Models
https://arxiv.org/abs/2104.08663

Useful lesson: dense retrieval is not automatically superior out of domain; candidate recall and reranking must be diagnosed separately.

### HyDE — Precise Zero-Shot Dense Retrieval without Relevance Labels
https://aclanthology.org/2023.acl-long.99/

Useful lesson: generated expansion can improve retrieval, but hallucinated properties are a real risk. This applies even more strongly to product-spec enrichment.

## Validation discipline

Repeated adaptation to a small benchmark can overfit its distribution. Harness v2 therefore separates roles:

- real-tender corpus — discovery and generalization evidence;
- public GOLD — detailed regression protection, but its case failures are hidden from research-first planning;
- adaptive hidden validation — rows remain secret; aggregate metrics protect against regressions;
- optional sealed final — distinct external dataset used only at campaign end.

A flat public/hidden benchmark does not by itself prove a new mechanism is useless. A regression still blocks promotion, while a flat safe candidate may reach the cheap validator for a real-tender value/mechanism judgment.
