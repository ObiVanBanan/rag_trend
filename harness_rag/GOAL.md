# Goal

Improve the usefulness, accuracy and reliability of the LD nomenclature-matching service on the current real tender workload.

The product task is simple: a user supplies a tender nomenclature line, the service should find the most appropriate LD catalog product or useful set of candidates. If the available evidence is insufficient or no suitable LD product exists, an honest `NOT_FOUND`/uncertain outcome is better than confidently returning an unrelated product.

The current matcher architecture is not sacred. Research and implementation may change query understanding, parsing, aliases, candidate generation, BM25/dense retrieval, fusion, reranking, DeepSeek usage, prompts, catalog/index representation, confidence logic, MCP/web enrichment, model choice, or the larger matching pipeline when evidence supports it.

## Primary optimization target

The primary working corpus is now `data/kontur_all_783_queries.json`: 783 current tender nomenclature rows processed by the real end-to-end pipeline.

This corpus is mostly unlabeled. Therefore the harness MUST NOT optimize raw `MATCHED` count as if it were accuracy. Instead it uses the 783 rows to expose recurring bottlenecks and compare the current champion with each viable candidate on exactly the same inputs:

- `QUERY_REJECTED`;
- `HARD_CONSTRAINT_FILTER` and the concrete constraint that rejects candidates (`dn`, `pn`, `joining_type`, `thread_type`, `body_material`, `bore_type`, `control`, etc.);
- `RERANK_NOT_FOUND`;
- `RERANK_FAILED`;
- `MATCHED`;
- web/MCP lookup attempts and accepted enrichment;
- per-row stage transitions and changed returned products.

A `NOT_FOUND -> MATCHED` transition is useful evidence only when the new LD result is technically defensible from the query and trusted evidence. A reduction in rejection/filter counts is diagnostic progress, not proof of correctness. The harness must reject mechanisms that create apparent coverage by weakening hard constraints, inventing missing facts, or returning unrelated products.

## Correctness guardrails

The existing manually verified public GOLD and adaptive hidden validation remain correctness and safety guardrails. They are intentionally smaller than the current workload and must not dominate hypothesis generation, but a candidate may not trade their correctness for broader apparent coverage.

Candidates that regress public/hidden coverage or safety are rejected before the expensive 783-row candidate evaluation. A candidate whose labeled guardrails remain flat-or-better may proceed to the current-dataset comparison and final mechanism/value validator.

The optional sealed final holdout remains separate and is evaluated only at campaign end under the existing supervisor rules.

## Supporting research corpus

The older Kontur research corpus remains useful supporting evidence:

- 253 unique real tender nomenclature rows;
- 90 high-confidence first-pass labels: 72 `MATCHED`, 18 `NOT_FOUND`;
- 130 unresolved/review cases;
- 33 probable `NOT_FOUND` cases that still require stronger catalog evidence.

These rows may be used to understand recurring failure classes and causal mechanisms, but they are no longer the primary optimization target.

Runtime lookup of a manufacturer/model/designation on the public internet through the MCP search path, followed by query enrichment before local LD retrieval, is an allowed hypothesis family. It is not a prescribed solution and should compete with simpler alternatives.

## Immediate campaign focus: constraint-aware recall with explicit identity safety

The next campaign should prioritize a clean, isolated reproduction of the strongest signal from the previous campaign: normal hybrid retrieval can miss LD products that already satisfy the deterministic hard-constraint evaluator.

The primary implementation hypothesis to test is:

1. run the existing hybrid retrieval path unchanged;
2. only when that pool contains zero candidates that pass the unchanged deterministic hard constraints, run a bounded lexical/full-catalog fallback;
3. apply the same existing hard-constraint evaluator to fallback candidates;
4. preserve strong explicit product identity from the original tender query before allowing a fallback candidate;
5. send the surviving bounded pool through the existing reranker.

Strong identity includes explicit article/model/designation/family tokens such as native LD designations or competitor model families. The implementation must be general and evidence-driven: do not special-case benchmark ids, returned LD ids, or one-off tender strings.

This experiment is intentionally narrow. Do not modify the query interpreter, web enrichment policy, reranker prompt/model, hard-constraint semantics, or index representation in the same candidate unless research proves the fallback experiment cannot be isolated without that change. A multi-mechanism patch is not acceptable evidence for this campaign focus.

The previous campaign produced a strong but concurrency-contaminated signal: many `HARD_CONSTRAINT_FILTER -> MATCHED` recoveries kept identical hard constraints, while one labeled regression (`tender_v1_033`) showed that fallback can be unsafe when an explicit designation such as `11с67п` is not preserved as identity. Treat this as a hypothesis and regression target, not as accepted causal truth.

Success evidence should include all of the following:

- labeled correctness/safety remains flat-or-better;
- prior recovered positive cases remain recoverable without hardcoding them;
- an identity-heavy query analogous to the `11с67п` regression cannot fall back to a different designation family merely because type/DN are compatible;
- the full 783-row comparison shows a mechanism-consistent excess of `HARD_CONSTRAINT_FILTER -> MATCHED` transitions above the champion A/A noise floor;
- recovered rows should usually retain the same hard constraints, demonstrating candidate-recall improvement rather than constraint weakening.

If this isolated mechanism fails these conditions, reject it and record whether the failure came from retrieval coverage, identity extraction/preservation, or insufficient catalog evidence.

## Research-first cycle

Every new attempt follows this sequence:

1. **Research** — inspect the current champion, durable experiment memory, the deterministic summary of the 783 current corpus, and supporting real-tender evidence. Identify the highest-value recurring bottleneck before selecting a hypothesis.
2. **Planner** — choose one bounded falsifiable implementation hypothesis, or stop with `DONE` when no credible direction remains.
3. **Implementer** — implement only that hypothesis. The default worker is the cheaper `gpt-5.5` with low reasoning.
4. **Cheap gates** — run deterministic tests and public/hidden correctness guardrails. Rebuild the index only when required.
5. **Current-dataset evaluation** — only surviving candidates are run across the full 783-row workload and compared row-for-row with the current champion.
6. **Cheap validator** — one `gpt-5.5` low read-only ACCEPT/REJECT mechanism/value check sees the 783 delta plus labeled guardrails.
7. **Promote or rollback** — an accepted candidate becomes the new champion. A rejected candidate rolls back to the current champion, not the original baseline.

There is no mandatory second research pass, no strong Reviewer pass and no Fixer loop in the default pipeline.

## Evolution semantics

The project evolves cumulatively:

`champion -> hypothesis -> cheap gates -> 783 comparison -> ACCEPT -> new champion -> next hypothesis`

or

`champion -> hypothesis -> gate/validator REJECT -> rollback to champion -> next hypothesis`.

Every new accepted experiment therefore builds on previously accepted improvements. The original baseline is historical reference, not the reset point for each cycle.

## Guardrails

- Preserve uncertainty: missing catalog evidence is not a negative fact and must not silently become a default.
- Do not hardcode benchmark ids, GOLD answers, known LD ids or one-off tender strings.
- Do not optimize unlabeled `MATCHED` count directly.
- Prefer mechanisms that plausibly improve a recurring class of current 783-row inputs.
- Do not return a product merely to avoid `NOT_FOUND`; low-value or technically incompatible matches are product failures.
- Treat changed-product and `MATCHED -> NOT_FOUND` transitions as regressions requiring explicit review.
- Infrastructure/provider failures do not count as scientific evidence and must preserve the active stage for resume.
- Respect agent-call, research-call and index-build budgets.
