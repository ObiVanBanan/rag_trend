# Experiments

Short decision history for frozen-100 DEV runs. Per-case traces and full outputs stay in `.tmp` or their original evaluation artifacts.

## E01 — Competitor decoder baseline
Commit: `08e2497`
Setup: frozen-100 DEV; decoder v1 baseline.
Hypothesis/change: decode competitor designations into technical facts before LD retrieval.
Metrics: top1 26%; initial GOLD recall 66%; rerank GOLD recall 67%.
Failure split: not preserved in the aggregate report.
Conclusion: decoder established the candidate-recall baseline.
Decision/next: compare resolver approaches against this baseline; keep GOLD hidden from source understanding.

## E02 — Competitor resolver v1
Commit: `df8b584` (initial resolver); result recorded in PR #13.
Setup: frozen-100 DEV; resolver integrated with the decoder path.
Hypothesis/change: resolve competitor facts from a persistent, provenance-aware KB/catalog before retrieval.
Metrics: top1 21%; initial GOLD recall 56%; rerank GOLD recall 55%.
Failure split: family identity could be stored and reused as an exact product; numeric stage counts were not preserved.
Conclusion: resolver integration regressed from the 26% / 66% / 67% decoder baseline; family and exact-product identity were conflated.
Decision/next: separate family from exact variant identity and prevent family facts from populating exact product records.

## E03 — Decoder-precedence gate diagnostic
Commit: `50b2b92`
Setup: same frozen-100 DEV evaluation; decoder precedence enabled over resolver v1.
Hypothesis/change: gate resolver use to avoid disturbing products handled by the deterministic decoder.
Metrics: top1 25%; initial GOLD recall 63%; rerank GOLD recall 64%; resolver strategy count 44 → 0.
Failure split: remaining gap was not split by pipeline stage in the saved summary.
Conclusion: the v1 regression came from resolver interaction with decoder-covered MARSHAL/ALSO cases; precedence hides the regression without solving source understanding.
Decision/next: retain the diagnostic as evidence, but make resolver-only exact identity the target architecture.

## E04 — Resolver v2, TEMPER schema
Commit: `0eedf9f`
Setup: frozen-100, seed 53, fresh KB, decoder OFF, runtime web ON.
Hypothesis/change: resolver-only exact identity plus a generic TEMPER catalog schema can resolve exact products without decoder logic.
Metrics: top1 8%; initial recall 22%; rerank recall 22%; TEMPER top1 4/8, initial 6/8.
Failure split: `RETRIEVAL_MISS` 78/100.
Conclusion: catalog/schema resolution works where catalog knowledge exists; missing MARSHAL/ALSO coverage dominates the regression.
Decision/next: add MARSHAL/ALSO catalog schemas. Do not tune the reranker while candidates are missing.

## E05 — Resolver v2, runtime web
Commit: `0eedf9f` (web-on run; decision documented in `56c1c15`).
Setup: same frozen-100, seed 53, fresh KB, decoder OFF; runtime web ON.
Hypothesis/change: use live search to recover unresolved competitor products and improve technical retrieval queries.
Metrics: web accepted 3; rejected 54; skipped 43; accepted results produced 0 useful technical-query paths.
Failure split: all 3 accepted contexts ended as `web_context_no_technical_query`; no new resolver retrieval path was produced.
Conclusion: runtime web added latency and result noise without demonstrated recall gain.
Decision/next: disable runtime web; retain search for offline knowledge acquisition. See [ADR-001](decisions/ADR-001-runtime-web-search.md).

## E06 — Resolver v2, MARSHAL and ALSO schemas
Commit: `f2d6cc1`
Setup: frozen-100, seed 53, fresh KB, decoder OFF, runtime web OFF, workers 1.
Hypothesis/change: generic data-driven designation schemas for MARSHAL and ALSO restore candidate recall without manufacturer-specific matcher logic.
Metrics: top1 30%; initial recall 63%; rerank recall 63%; catalog-resolved strategy 91/100; web skipped 100/100. MARSHAL initial 30/42, top1 13/42; ALSO 27/47, 13/47; TEMPER 6/8, 4/8.
Failure split: `RETRIEVAL_MISS` 37; `RERANK_SELECTION` 32; `FILTER_DROP` 1; success 30.
Conclusion: initial recall rose from 22% to 63%, close to the 66% decoder baseline; top1 rose from 8% to 30%. Candidate coverage is now strong enough to expose downstream selection failures.
Decision/next: keep runtime web off and retain the generic schemas. Analyze the 32 rerank-selection failures separately; leave the final unseen holdout untouched.
