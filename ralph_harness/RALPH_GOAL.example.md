# Goal

Improve the project against the configured evaluation evidence through small, causal, reproducible experiments.

The current implementation is a baseline/champion, not a final architecture.

For every experiment:

1. identify the highest-value uncertainty or bottleneck;
2. formulate one bounded falsifiable hypothesis;
3. implement only the change needed to test it;
4. run deterministic tests and gate evaluators;
5. inspect expensive or unlabeled evidence only after gates pass;
6. compare the candidate with the current champion;
7. ACCEPT only when the mechanism and evidence are defensible, otherwise rollback;
8. preserve the result and lesson for the next experiment.

Do not optimize a vanity metric, weaken correctness constraints to improve apparent coverage, or make case-specific patches unless they establish a general mechanism.

Return DONE when the goal is already met or when remaining experiments have low expected information/value relative to their cost.
