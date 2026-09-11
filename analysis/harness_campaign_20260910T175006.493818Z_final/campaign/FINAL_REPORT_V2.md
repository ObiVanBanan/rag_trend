# RAG Harness v2 Final Report

- Outcome: **MAX_SCIENTIFIC_ITERATIONS**
- Attempts started: **5**
- Scientific iterations: **5** / 5
- Champion commit: `e1242be4d4f203063e5c1bd489b1878179454256`
- Public hard-pass: **0.8333333333333334**
- Hidden-validation hard-pass: **0.8666666666666667**
- Sealed final hard-pass: **not run**
- Agent calls: **15** / 28
- Planner / Implementer / Reviewer / Fixer / Research: **5 / 5 / 0 / 0 / 5**
- Reported Codex tokens (when available): **951539**

## Campaign history

- Attempt 1: **REJECTED** [query_evidence_preservation] experiment=`query-evidence-preservation-1c451c1cd8` science=1 — Repair generic quantity/clause token boundaries so canonical retrieval alternates retain complete alphanumeric technical designations instead of truncating at embedded quantity-like fragments. (public=0.8333333333333334, hidden=0.8666666666666667, code=METRIC_GATE_REJECT)
- Attempt 2: **REJECTED** [query_variant_evidence_fusion] experiment=`query-variant-evidence-fusion-bd197b87b9` science=2 — Preserve raw-query and canonical-query retrieval hits as separate RRF evidence channels so cross-variant candidate corroboration affects ranking without changing reranker inputs or final selection. (public=0.8666666666666667, hidden=0.8666666666666667, code=PUBLIC_REGRESSION)
- Attempt 3: **REJECTED** [line_item_product_decomposition] experiment=`line-item-product-decomposition-a401b3ca70` science=3 — Extract an explicit product-bearing span from a narrowly recognized mixed work-and-product tender clause, using it only as the existing additive retrieval alternate while preserving raw-query retrieval and final selection semantics. (public=0.8333333333333334, hidden=0.8666666666666667, code=METRIC_GATE_REJECT)
- Attempt 4: **REJECTED** [catalog_scope_uncertainty] experiment=`catalog-scope-uncertainty-24c6f98c40` science=4 — Add a narrowly affirmative, fail-open catalog-scope route that returns NOT_FOUND with reason NO_CATALOG_EVIDENCE for clearly unsupported consumable, water-treatment, and service-only requests. (public=0.8333333333333334, hidden=0.8666666666666667, code=METRIC_GATE_REJECT)
- Attempt 5: **REJECTED** [uncertainty_preserving_result_set_contract] experiment=`uncertainty-preserving-result-set-contract-207dd0f2fd` science=5 — Expose the reranker’s existing ordered selections as a production `candidate_set` with product identity, confidence, and reason, while preserving the current primary product and binary outcome semantics. (public=0.8333333333333334, hidden=0.8666666666666667, code=METRIC_GATE_REJECT)
