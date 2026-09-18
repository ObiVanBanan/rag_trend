## 1. Deterministic provenance correction

- [x] 1.1 Add a small, pure native-designation helper and an explicitly documented
  allowlist/configuration grammar in `query_interpreter.py`; invoke it only while
  sanitizing a model-emitted exact `valve_designation`.
- [x] 1.2 Preserve all other parsed constraints and retrieval text, and attach a
  compact debug provenance marker when the helper changes the designation gate.

## 2. Focused regression coverage

- [x] 2.1 Add interpreter unit tests for representative compact native-family forms,
  punctuation/case variants, and retention of independently explicit DN, PN,
  joining, bore, material, and control values.
- [x] 2.2 Add negative tests proving no rewrite for competitor designations,
  unsupported native-family-like tokens, standalone family names, malformed suffixes,
  or an interpreter designation that does not match the tender composite.
- [x] 2.3 Add matcher-level coverage proving the corrected family token is passed to
  existing exact eligibility/reranking while original query and normalized retrieval
  text remain unchanged.

## 3. Verification

- [x] 3.1 Run the focused query-interpreter, matcher, and constraint tests, then
  `python -m pytest -q`.  Do not rebuild the index.  The outer supervisor owns public,
  hidden, and 783-row evaluation.
