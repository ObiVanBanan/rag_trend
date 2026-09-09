# Goal

Improve the accuracy and reliability of the current LD nomenclature-matching MVP through measured autonomous experimentation.

The harness starts from the current baseline and runs at most 15 research cycles. Each cycle should form one falsifiable hypothesis, implement it, review it, measure it, and either promote it as the new champion or reject it.

## Primary objective

Maximize the measured quality of the current MVP. The Planner is deliberately free to change any implementation component when evidence suggests it can improve the result: query processing, normalization, parsing, structured constraints, candidate generation, BM25/dense retrieval, fusion, reranking, DeepSeek usage, prompts, business logic, model choice, index representation, thresholds, or the overall matching architecture.

The existing architecture is not sacred. The failure taxonomy and research references are evidence and idea sources, not a mandatory implementation order.

Do not optimize by hardcoding a known test id, exact GOLD answer, or one-off lookup for a single benchmark string. Prefer changes that plausibly improve a class of real tender inputs. A hypothesis may be narrow enough to test cleanly, but it must be justified as an MVP improvement rather than a patch for one case.

## Success and guardrails

Success means:

- blind final-check hard-pass coverage reaches at least 93%;
- a candidate must not regress blind coverage versus the current champion;
- false matches, explicit human-reject failures, and other safety failures must not regress;
- public metrics are used as detailed development feedback and should improve where possible;
- the project remains testable and every promoted change is supported by measured evidence;
- no more than five full index rebuilds are used across the entire run.

The 93% value is a success target, not a requirement to burn all 15 cycles. The Planner may stop early below 93% when the complete experiment history and remaining evidence no longer suggest any credible new experiment with positive expected metric gain or useful information gain. It should prefer a clear `DONE`/exhausted conclusion over inventing a weak or repetitive hypothesis merely to spend another cycle.

The Planner must use the complete experiment history: previous hypotheses, decisions, public/blind metric results, rejection reasons, and lessons. Repeating a failed idea is acceptable only when new evidence materially changes the hypothesis.

The final holdout must live outside the repository and must never be shown to Planner, Implementer, Reviewer, or Fixer. Agents may receive only aggregate final-check metrics produced by the supervisor.
