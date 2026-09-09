## Context

See `proposal.md` for motivation. Cycle 3 added a pure canonicalization boundary before hybrid retrieval. It currently handles compact DN/PN notation, several Russian joining abbreviations, and mixed-script designations, but does not recognize `WW`. Hybrid retrieval already treats a canonical query additively, deduplicates by LD id inside each modality, and reranks using the original source query.

## Goals / Non-Goals

**Goals:**

- Extend the existing pure canonicalization boundary for a narrowly defined, unambiguous weld-weld connection code.
- Maintain a single alternate query and current original-query fail-open behavior.
- Make the token-boundary and preservation rules independently testable.

**Non-Goals:**

- No mapping from a code or designation to an LD product, product family, or catalog ID.
- No expansion of ambiguous codes such as `FF`/`MF`, whose meanings depend on product and thread context.
- No parser, reranker, ranking, candidate filtering, threshold, prompt, catalog, index, or schema change.

## Decisions

### Use a conservative standalone-token allowlist

Recognize `WW` only when bounded away from letters and digits, case-insensitively, and replace/augment it with the catalog-native Russian welded-connection terms already understood by retrieval. This preserves all non-connection tokens; it does not infer an end geometry, material, pressure, or product class.

The alternative of broadly translating pairs of Latin letters is rejected because `FF` and `MF` are ambiguous between flanged and thread-gender semantics. An LLM rewrite is also rejected because it may invent engineering properties and cannot offer the same deterministic boundary guarantees.

### Keep canonicalization additive through the existing hybrid path

Continue querying original text regardless of whether notation expansion produces an alternate. The pre-existing per-modality deduplication/RRF behavior applies without change, and reranking receives the source tender text. This isolates the experiment to candidate recall and preserves the outcome semantics that were safe in Cycle 3.

Changing candidate score fusion or using canonical text as the reranker input is rejected because either would confound the effect of notation expansion and revisit the selection-adjacent risks observed in Cycles 1 and 2.

## Risks / Trade-offs

- [A `WW` substring is part of a product code rather than a connection] → Require strict token boundaries and tests for embedded forms.
- [Welded vocabulary makes an otherwise unsuitable candidate more visible] → Keep source retrieval and reranker semantics unchanged; outer evaluation controls promotion.
- [The extra query adds latency] → Reuse Cycle 3's at-most-one-alternate path and emit none when the representation would be unchanged.
- [The code family is narrower than blind variation] → Treat this cycle as a falsifiable information-gain experiment; do not broaden to ambiguous codes without evidence.

## Migration Plan

1. Add the standalone `WW` transformation to the existing pure canonicalization flow.
2. Add focused canonicalization, hybrid fallback/deduplication, and matcher source-preservation tests.
3. Run the focused tests and `python -m pytest -q`.
4. The outer supervisor performs all public/blind evaluation and promotion decisions.
5. Roll back by removing the allowlist entry; no persisted data, index, or migration needs rollback.
