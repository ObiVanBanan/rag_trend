# Goal

Improve the LD nomenclature matching project through a bounded autonomous research loop without overfitting to the final check.

The harness starts by measuring the current solution, then runs at most 15 research cycles. Each cycle must test one bounded hypothesis, implement it, review it, optionally fix it, and measure the result.

Success means:

- the blind final-check coverage is at least 93%;
- coverage never regresses versus the current champion while searching for improvements;
- false matches and explicit human-reject failures do not get worse;
- the project remains testable and the change is explainable by evidence from the run;
- no more than five full index rebuilds are used across the entire harness run.

The planner should focus first on the failure modes already evidenced in the repository: designation/alias mapping, parser/schema gaps, catalog representation, then retrieval/reranking. It should not assume that every unresolved query is a retrieval failure.

The final holdout must live outside the repository and must never be shown to Planner, Implementer, Reviewer, or Fixer. Agents may receive only aggregate final-check metrics produced by the supervisor.
