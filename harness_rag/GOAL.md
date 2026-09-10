# Goal

Improve the usefulness, accuracy and reliability of the LD nomenclature-matching service on real incoming tender nomenclature.

The product task is simple: a user supplies a tender nomenclature line, the service should find the most appropriate LD catalog product or useful set of candidates. If the available evidence is insufficient or no suitable LD product exists, an honest `NOT_FOUND`/uncertain outcome is better than confidently returning an unrelated product.

The current matcher architecture is not sacred. Research and implementation may change query understanding, parsing, aliases, candidate generation, BM25/dense retrieval, fusion, reranking, DeepSeek usage, prompts, catalog/index representation, confidence logic, external enrichment, model choice, or the larger matching pipeline when evidence supports it.

## Research-first cycle

Every new attempt follows one simple sequence:

1. **Research** — inspect the current champion, durable experiment memory and the real-tender working corpus. Use external sources only when useful. Identify recurring failure classes and promising solution directions before a hypothesis is selected.
2. **Planner** — use the fresh research and the product goal to choose one bounded falsifiable implementation hypothesis, or stop with `DONE` when no credible direction remains.
3. **Implementer** — implement only that hypothesis. The default worker is the cheaper `gpt-5.5` with low reasoning.
4. **Test + evaluation** — run deterministic tests and the existing public/hidden regression gates. Rebuild the index only when required.
5. **Cheap validator** — only candidates that survive the metric guardrails receive one `gpt-5.5` low read-only ACCEPT/REJECT mechanism/value check.
6. **Promote or rollback** — an accepted candidate becomes the new champion. A rejected candidate rolls back to the current champion, not the original baseline.

There is no mandatory second research pass, no strong Reviewer pass and no Fixer loop in the default pipeline.

## Real-tender working corpus

The primary material for discovering what to improve is the new Kontur tender corpus:

- 253 unique real tender nomenclature rows;
- 90 high-confidence first-pass labels: 72 `MATCHED`, 18 `NOT_FOUND`;
- 130 unresolved/review cases;
- 33 probable `NOT_FOUND` cases that still require stronger catalog evidence.

The 90 first-pass labels are useful supporting evidence but are not claimed to be exhaustive final truth. The 130 + 33 unresolved cases are especially valuable for discovering missing capabilities. Research should reason about recurring classes of inputs rather than memorize individual rows.

Runtime lookup of a manufacturer/model/designation on the public internet, followed by query enrichment before local LD retrieval, is an allowed hypothesis family when the data supports it. It is not a prescribed solution and should compete with simpler alternatives.

## Benchmark role

The existing manually verified public GOLD and adaptive hidden validation are **regression guardrails**, not the main research target. They are intentionally small and should not dominate hypothesis generation.

Agents must not mine benchmark answer ids or one-off benchmark strings to choose fixes. The Planner receives aggregate benchmark metrics but not the public case-level failure list under the research-first runner.

A candidate is rejected if it materially violates the existing public/hidden coverage or safety gates. A candidate whose old benchmark stays safely flat may continue to the cheap validator, because its main value may be on broader real-tender inputs not represented by the small benchmark.

The optional sealed final holdout remains separate and is evaluated only at campaign end under the existing supervisor rules.

## Evolution semantics

The project evolves cumulatively:

`champion -> hypothesis -> evaluate -> ACCEPT -> new champion -> next hypothesis`

or

`champion -> hypothesis -> evaluate -> REJECT -> rollback to champion -> next hypothesis`.

Every new accepted experiment therefore builds on previously accepted improvements. The original baseline is historical reference, not the reset point for each cycle.

## Guardrails

- Preserve uncertainty: missing catalog evidence is not a negative fact and must not silently become a default.
- Do not hardcode benchmark ids, GOLD answers, known LD ids or one-off tender strings.
- Prefer mechanisms that plausibly improve a class of real tender inputs.
- Do not return a product merely to avoid `NOT_FOUND`; low-value or technically incompatible matches are product failures.
- Infrastructure/provider failures do not count as scientific evidence and must preserve the active stage for resume.
- Respect agent-call, research-call and index-build budgets.
