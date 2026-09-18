## Why

The A/A repeat exposes a recurrent, mechanism-specific instability in compact native
LD designation rows.  For example, tender text containing `КШЦФ` followed by a
numeric configuration code can be interpreted as an exact composite
`valve_designation`.  The catalog uses the family token, so the composite becomes a
false hard gate and otherwise compatible candidates are filtered out.  This is a
query-constraint provenance defect, not evidence that constraints should be relaxed
or that the catalog lacks a product.

Cycle 7 attempted a broader sanitizer but produced no attributed value.  Its
postmortem requires payload-shape coverage before a retry.  The new experiment is
therefore limited to a directly observable native-family pattern and adds regression
coverage at the interpreter boundary.

## What Changes

- Add a deterministic sanitizer for an allowlisted native LD designation family when
  it is immediately followed by a compact numeric configuration suffix.
- Replace only the exact-designation constraint with the family token; retain all
  independently extracted DN, PN, joining, bore, material, control, and product-type
  constraints unchanged.
- Preserve unrelated native designations, competitor model/designation handling, and
  all retrieval, enrichment, reranking, and eligibility semantics.

## Capabilities

### New Capabilities

- `designation-family-provenance`: constrains exact LD designation gates to an
  evidenced family token rather than an adjacent non-identity configuration suffix.

### Modified Capabilities

- None.

## Impact

- Affected code: `src/nomenclature_matcher/query_interpreter.py` and focused tests.
- No prompt, catalog, index, embedding, Qdrant payload, retrieval limit, or harness
  change is required; no index rebuild is expected.
