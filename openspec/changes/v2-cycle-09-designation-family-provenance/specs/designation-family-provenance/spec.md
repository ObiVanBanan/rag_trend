## Purpose

Prevent a compact, non-identity numeric configuration suffix adjacent to an evidenced
native LD designation family from becoming an unsupported exact-designation gate.

## ADDED Requirements

### Requirement: Normalize supported native designation-family constraints

When a tender query contains an allowlisted native LD designation family followed by
a supported compact numeric configuration suffix, and the interpreted
`valve_designation` is that same composite designation, the system SHALL use only the
family token for exact-designation eligibility.  It SHALL retain every independently
extracted technical constraint and SHALL retain source query and normalized retrieval
text.

#### Scenario: Compact native family configuration

- **WHEN** a tender contains an allowlisted LD family followed by a numeric-dot
  configuration suffix and the interpreter returns their composite as
  `valve_designation`
- **THEN** the hard exact-designation constraint contains the family token and DN,
  PN, joining, bore, material, control, and product-type fields are unchanged

#### Scenario: Corrected-gate provenance

- **WHEN** the family constraint is corrected
- **THEN** debug interpretation data identifies that the correction occurred without
  representing the configuration suffix as a technical requirement

### Requirement: Fail closed for unsupported designation forms

The system SHALL NOT remove, replace, or infer a designation for competitor models,
unallowlisted family-like tokens, standalone family names, malformed suffixes, or
when the interpreter's designation does not correspond to the tender's supported
composite form.

#### Scenario: Competitor model

- **WHEN** a competitor designation has an adjacent numeric suffix
- **THEN** the system does not convert it to an LD designation or alter its
  designation constraint on the basis of this capability

#### Scenario: Near miss

- **WHEN** a native-looking token lacks the supported configuration grammar or the
  interpreted designation does not match the source composite
- **THEN** the system leaves the designation constraint unchanged
