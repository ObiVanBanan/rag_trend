## Context

`DeepSeekQueryInterpreter._sanitize_payload` already makes tender-explicit DN,
thread orientation, and control deterministic after model interpretation.  Exact
`valve_designation` remains eligible to become a hard constraint.  In compact
native-LD strings, an adjacent numeric configuration suffix is not catalog identity,
but it can be emitted as part of that constraint.  `evaluate_product` correctly
enforces the resulting constraint, which makes this a provenance issue upstream of
retrieval and reranking.

## Decision

Add a small pure helper used by `_sanitize_payload` after the model payload is read:

- Match only a documented allowlist of native LD family tokens followed by an
  adjacent numeric-dot configuration suffix, with case-insensitive boundaries.
- If the interpreted exact designation is the same composite form (including
  punctuation-insensitive representation), set it to the family token.
- Do not derive a designation from a competitor token, do not map any designation to
  a catalog product or product type, and do not change other constraint fields.
- Record a compact interpreter warning/provenance marker in the existing payload
  comment/reasoning surface suitable for debug serialization, without exposing it as
  a new hard fact.

The implementation must not rewrite source tender text or normalized retrieval text.
The existing raw query and candidate flow remain authoritative.

## Alternatives Considered

- **Candidate fallback:** previously improved labeled guardrails but created
  MATCHED-to-NOT_FOUND regressions outside its trigger surface; it does not explain
  the compact designation field defect.
- **Web attribution:** useful for a later causal audit but would not correct this
  native-LD, source-text-grounded payload before exact filtering.
- **Broad designation aliases:** rejected because supporting taxonomy explicitly says
  competitor/legacy equivalences are not yet validated.  This change recognizes only
  an already-native LD family and removes no technical constraint.

## Risks and Mitigations

- A numeric suffix might itself be a meaningful product identity.  Limit the rule to
  an allowlisted family plus the observed compact configuration grammar, require the
  model-emitted constraint to match that composite, and test near misses.
- A correction could hide a true incompatible designation.  Keep exact family gating
  and every separately explicit engineering constraint intact; outer guardrails decide
  promotion.
- The effect could be within A/A variance.  Require the emitted constraint and
  changed eligibility path to be visibly tied to this helper, not just an aggregate
  MATCHED increase.
