# RAG Harness Final Report

- Outcome: **MAX_CYCLES**
- Cycles completed: **17** / 17
- Index rebuilds: **2** / 5
- Champion commit: `d0712a9f39994d687f3b558e7ef9ba0677ef7aec`
- Champion Qdrant collection: `default from environment/settings`
- Public hard-pass rate: **0.8333333333333334**
- Blind hard-pass rate: **0.8666666666666667**
- Blind success floor: **93.00%**

## Hypotheses

- Cycle 1: **REJECTED** — Filtering retrieved candidates that contradict explicit tender constraints before LLM reranking will reduce unsafe positive matches and improve hard-pass coverage. (public=0.7333333333333333, blind=0.6333333333333333)
- Cycle 2: **REJECTED** — Advisory per-candidate compatibility evidence in reranker context will reduce unsafe specialized-variant selections without reducing candidate recall. (public=0.8, blind=0.8333333333333334)
- Cycle 3: **ACCEPTED** — Deterministic, domain-preserving query canonicalization before hybrid retrieval will improve matching of compact, mixed-script, and noisy tender nomenclature without changing final-selection semantics. (public=0.8333333333333334, blind=0.8666666666666667)
- Cycle 4: **ACCEPTED** — Expanding standalone WW (weld-weld) notation into Russian welded-connection vocabulary in the additive retrieval query will improve recall for terse technical tenders without changing selection semantics. (public=0.8333333333333334, blind=0.8666666666666667)
- Cycle 5: **REJECTED** — An evidence-first DeepSeek reranker rubric will reduce technically conflicting or unprovable final selections by prioritizing explicit tender requirements and catalog evidence over retrieval rank. (public=0.8333333333333334, blind=0.8666666666666667)
- Cycle 6: **REJECTED** — Cleanly canonicalizing unambiguous repeated Russian flanged-end shorthand such as `фл/фл` into one catalog-native connection term improves candidate recall without changing selection semantics. (public=0.8333333333333334, blind=0.8666666666666667)
- Cycle 7: **REJECTED** — A deterministic, execution-aware catalog document representation will improve retrieval of ordinary versus specialised ball-valve variants while preserving selection semantics. (public=0.8333333333333334, blind=0.8)
- Cycle 8: **REJECTED** — A fail-open deterministic post-rerank compatibility fallback will reduce explicit execution/control/bore conflicts by substituting only an already retrieved, fully evidenced compatible candidate. (public=0.8333333333333334, blind=0.8666666666666667)
- Cycle 9: **REJECTED** — Explicit ball-valve standard-execution wording can be deterministically expanded into standard-bore catalog vocabulary to improve candidate recall without changing selection semantics. (public=0.8333333333333334, blind=0.8666666666666667)
- Cycle 10: **REJECTED** — Explicitly model and affirmatively verify flange construction subtype so generic or flat flanges cannot satisfy weld-neck/butt-weld requirements. (public=0.8333333333333334, blind=0.8666666666666667)
- Cycle 11: **IMPLEMENTATION_FAILED** — A guarded, standards-backed outer-pipe-diameter-to-DN retrieval alternate will improve candidate recall for valve tenders that specify an unambiguous pipe OD rather than DN, while preserving original-query reranking and selection. (public=0.8333333333333334, blind=0.8666666666666667)
- Cycle 12: **IMPLEMENTATION_FAILED** — Explicit flange construction subtype verification will prevent unproved flat, loose, counter, or generic flanges from satisfying weld-neck/butt-weld requirements. (public=0.8333333333333334, blind=0.8666666666666667)
- Cycle 13: **IMPLEMENTATION_FAILED** — Diversifying structurally redundant hybrid candidates before the fixed reranker budget will surface viable ordinary/control variants currently crowded out by near-duplicate specialised variants. (public=0.8333333333333334, blind=0.8666666666666667)
- Cycle 14: **IMPLEMENTATION_FAILED** — A deterministic, additive retrieval representation that distinguishes an installed electric actuator from actuator-ready intent will improve candidate recall for explicit ball-valve control-state requests without changing final-selection behavior. (public=0.8333333333333334, blind=0.8666666666666667)
- Cycle 15: **IMPLEMENTATION_FAILED** — Explicit installed-electric versus actuator-ready wording can produce a bounded alternate retrieval query that improves recall without changing selection behavior. (public=0.8333333333333334, blind=0.8666666666666667)
- Cycle 16: **REJECTED** — Diversifying structurally redundant hybrid candidates before the fixed reranker budget will surface viable variants currently crowded out by near-duplicate catalog entries. (public=0.8, blind=0.8666666666666667)
- Cycle 17: **REJECTED** — Explicit installed-electric versus actuator-ready ball-valve wording can produce one bounded state-specific retrieval alternate that improves recall without changing selection behavior. (public=0.8666666666666667, blind=0.8666666666666667)

If the champion uses a temporary `rag_harness_*` Qdrant collection, promote/rebuild that selected champion into the normal deployment collection after research. The deployment rebuild is not part of the experimental five-build budget.
