## Context

The repository already contains most of the building blocks needed for the revised MVP: LD product models, search text construction, CSV-based LD loading, Qdrant collection management, OpenAI embeddings, dense candidate search, BM25 search, hybrid RRF retrieval, LLM reranking, duplicate-aware `match_many`, and pytest coverage around matcher behavior. The current code is CLI-first and local-file oriented; `tz.md` names PostgreSQL as the business source, while the repository currently uses `ld_products_full_nomenclature.csv` for local indexing.

The design treats "RAG" in this change as retrieval-augmented business mapping with LLM-based candidate judgment, not free-form answer generation. Development tests showed that dense cosine similarity alone is not sufficient for the business requirement, so the system must use hybrid retrieval plus reranking and let the LLM choose the best candidate or reject all candidates.

## Goals / Non-Goals

**Goals:**

- Make the hybrid retrieval, reranking, and LLM selection path satisfy the business mapping contract as a reusable batch interface.
- Keep implementation compatible with the current CSV/Qdrant development workflow while leaving a clear adapter point for PostgreSQL product loading.
- Return minimal production results that are usable by later tender pipeline or UI integration without adding those integrations now.
- Return extended development/debug details for manual review and quality evaluation.
- Make retrieval limits, candidate evidence, reranker behavior, and LLM selection outputs explicit enough for quality experiments.
- Maintain an experiment history that connects each quality hypothesis to a concrete run configuration, metrics, detailed results, and conclusion.
- Add validation and tests around business-visible behavior rather than only internal helper behavior.

**Non-Goals:**

- Add free-form LLM answer generation, structured query parsing, DN/PN filters, material rules, or tender Excel integration.
- Replace Qdrant or the existing embedding provider.
- Build a production HTTP service unless implementation discovers that a thin local service entrypoint is necessary for the immediate consumer.

## Decisions

### Use Hybrid Retrieval Instead Of Dense-Only Search

Use hybrid retrieval as the primary candidate-generation path. Dense vector search captures semantic similarity, while BM25 captures exact and near-exact lexical evidence such as product type, DN/PN tokens, article fragments, and joining type wording. Reciprocal-rank fusion gives a single candidate list without requiring score calibration between vector similarity and BM25.

Alternative considered: keep dense cosine similarity as the primary decision signal. Testing showed it is not accurate enough for business matching, especially when generic wording or close product variants appear.

### Use Reranking And LLM Selection For The Final Decision

The final decision should be made after reranking the hybrid candidates and asking the LLM to compare the original query with the candidate evidence. The LLM must choose the best candidate only when it is suitable and otherwise return `NOT_FOUND`, with a reason and confidence when available.

Alternative considered: select the fused top-1 candidate automatically. That repeats the core failure mode of score-only matching and does not use product evidence deeply enough for ambiguous tender strings.

### Split Production And Development Payloads

Expose a minimal production selected product with only `name` and `article`, because those are the fields needed by the downstream business mapping. Keep extended LD fields, retrieval scores, ranks, candidate ids, and LLM reasoning in a development/debug payload for review, threshold tuning, and error analysis.

Alternative considered: return a simple dictionary mapping source strings to product names. That loses score, status, LD id, and debug candidates, which `tz.md` calls technically preferable.

### Use Unique Normalized Queries For Work Reduction, Preserve Original Cardinality

Retain the duplicate-query optimization but ensure behavior is defined around non-blank normalized text. Results should preserve the original input order and count, including repeated items and blanks.

Alternative considered: search every list item independently. That is simpler but wastes embedding and Qdrant calls on repeated tender nomenclature lines.

### Keep CSV Indexing For Local MVP, Add PostgreSQL As A Loader Boundary

The current repository can already build the Qdrant index from `ld_products_full_nomenclature.csv`. Implementation should not block the MVP on PostgreSQL connectivity; instead, it should isolate product loading so PostgreSQL can become the source without changing search, indexing, or result behavior.

Alternative considered: require PostgreSQL immediately. That matches the long-term source of truth but risks delaying verification of the retrieval and threshold behavior that this change is meant to prove.

### Treat Thresholds As Review Controls, Not The Primary Decision

Use retrieval limits and any score thresholds as configurable controls for candidate generation and review, but not as the only production match decision. The primary production decision is the LLM selection result over the reranked candidate set.

Alternative considered: choose a fixed production cosine threshold in code. Current quality findings show that cosine thresholding alone cannot express the business decision reliably.

### Evaluate The Pipeline By Stage

Quality evaluation should report stage-level metrics, not only a final pass/fail number. Retrieval metrics show whether the right LD product appears in dense, BM25, and hybrid candidate sets. Reranker and LLM metrics show whether the correct candidate is selected or correctly rejected after it is available. This separation makes it clear whether to improve indexing/search text, retrieval fusion, reranking, or the LLM prompt/model.

The primary business risk is `WRONG_NOT_FOUND`: returning `NOT_FOUND` when a suitable LD product actually exists. This means retrieval should optimize for high recall, reranking should avoid dropping plausible candidates too aggressively, and the LLM prompt should choose a reasonable compatible candidate when the evidence supports it. `FALSE_MATCH` and wrong product selection remain important, but they are secondary to missing an existing product for this MVP.

The first evaluation target is a measured baseline, not a hard quality gate. A candidate RAG configuration should produce a tracked run with metrics, error categories, and a written conclusion before the team decides whether to set minimum pass/fail thresholds. This avoids inventing thresholds before enough reviewed examples and failure patterns are available.

The existing `data/eval_*` files and `scripts/eval.py` provide the current evaluation base. Implementation should keep producing machine-readable JSON with per-query rows, aggregate metrics, and error categories such as retrieval miss, wrong LLM selection, wrong `NOT_FOUND`, false match, reranker failure, and LLM error. Reports should expose `wrong_not_found_rate` separately from final accuracy so a high aggregate score cannot hide the main business failure.

Alternative considered: manually inspect only a few example searches. That is useful during debugging but does not provide comparable quality measurements across pipeline changes.

### Track Experiments In `data/experiments/`

Use `data/experiments/` as the durable log for RAG quality work. Each experiment should have a small human-readable record and machine-readable results, for example `data/experiments/YYYY-MM-DD-short-name/experiment.md` plus `results.json`. The record should capture the hypothesis, dataset, retrieval parameters, Qdrant indexing configuration, reranker settings, LLM model/settings, system prompt version, run timestamp, metrics, failure summary, conclusion, and next action.

Search strategy, Qdrant indexing, and prompt text are all experiment variables. Even if the current Qdrant indexing approach looks optimal, future experiments may change search text composition, payload fields, embedding model/dimension, collection aliases, vector names, TOP-K limits, fusion parameters, reranker prompts, or LLM system prompt rules. Each run should record enough configuration to compare before/after quality without guessing what changed.

The LLM system prompt should live in a versioned project file rather than only inline code. Experiment records should store the prompt path or identifier and, when practical, a content hash so prompt-only changes are visible in the experiment history.

Alternative considered: keep experiment notes only in chat or commit messages. That loses the link between a hypothesis, exact run settings, and observed metrics.

## Risks / Trade-offs

- Hybrid retrieval may still miss products when both semantic and lexical evidence fail -> Mitigation: retain dense, BM25, fusion ranks, and evaluation artifacts so misses can be reviewed against the full catalog.
- LLM selection can return `NOT_FOUND` too often and miss existing products -> Mitigation: tune prompts and candidate limits around `WRONG_NOT_FOUND` as the primary business metric.
- LLM selection can choose an incorrect candidate from plausible alternatives -> Mitigation: constrain prompts to the provided candidate set, require `NOT_FOUND` only when evidence is insufficient, store reasons/confidence, and evaluate against reviewed labels.
- LLM API errors can block final selection -> Mitigation: return a distinct failure status or controlled `NOT_FOUND` behavior for operational handling, and keep the reranked candidates for manual review.
- Experiment records can become inconsistent if run settings are not captured -> Mitigation: write a fixed experiment template and include retrieval, index, reranker, LLM, prompt, dataset, and result paths for each run.
- Prompt or index changes can appear to improve quality because another variable changed at the same time -> Mitigation: record every changed variable and prefer one primary hypothesis per experiment.
- CSV and PostgreSQL sources can diverge -> Mitigation: keep product loading separate from index/search behavior and document which source was used for each index build.
- Qdrant payload shape drift can break result payloads -> Mitigation: validate candidate field extraction in unit tests and keep missing optional LD fields as null rather than failing matches.
- Duplicate handling based on raw text can miss semantically identical strings with whitespace differences -> Mitigation: normalize whitespace before caching and preserve the original query text in externally returned results if consumers require exact echoing.

## Migration Plan

1. Confirm current hybrid retriever, reranker, and LLM selection behavior against the new spec with unit tests.
2. Add any missing serialization or batch script/service entrypoint needed to consume `list[str]` and emit production plus development JSON result objects.
3. Keep the existing index build flow operational for CSV-based local validation.
4. Add a PostgreSQL product loader only if implementation scope confirms database access/configuration is available; otherwise document CSV as the MVP source adapter and leave PostgreSQL as a follow-up behind the same loader interface.
5. Add or update the evaluation workflow so each run can be saved under `data/experiments/` with its hypothesis, retrieval configuration, Qdrant index configuration, system prompt version, metrics, and conclusion.
6. Validate with `python -m pytest -q` and, when Qdrant/LLM credentials are available, run a manual hybrid-rerank smoke test and one tracked evaluation experiment.

## Open Questions

- Does the immediate consumer need a Python API only, a CLI that accepts JSON input, or an HTTP endpoint? The core batch interface can be implemented first without changing the spec.
