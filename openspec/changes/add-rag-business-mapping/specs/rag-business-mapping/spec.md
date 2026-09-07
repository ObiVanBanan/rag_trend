## Purpose

Defines the MVP behavior for mapping tender nomenclature strings to LD catalog products using indexed LD retrieval, hybrid candidate search, reranking, and LLM-assisted business match decisions.

## ADDED Requirements

### Requirement: Batch nomenclature mapping input
The system SHALL accept a list of nomenclature strings as the MVP mapping input and SHALL produce one result object for every input item in the same order.

#### Scenario: Multiple input strings are mapped independently
- **WHEN** the caller submits three nomenclature strings
- **THEN** the system returns three result objects in the same order as the submitted strings

#### Scenario: Blank input string is not matched
- **WHEN** an input item is empty or contains only whitespace
- **THEN** the corresponding result has status `NOT_FOUND` and no LD product

### Requirement: Retrieval uses the indexed LD catalog
The system SHALL search against the previously indexed LD catalog and SHALL NOT re-vectorize the full LD catalog during per-request matching.

#### Scenario: User matching does not rebuild the catalog index
- **WHEN** the caller submits nomenclature strings for matching after the LD index has been built
- **THEN** the system embeds only the submitted query text needed for matching and searches the existing LD index

### Requirement: LD search document content
The system SHALL build LD search documents from business-relevant product fields including name, article, DN, PN, joining type, and product properties, while excluding service metadata from the embedded search text.

#### Scenario: Business product fields are searchable
- **WHEN** an LD product contains name, article, DN, PN, joining type, and product properties
- **THEN** the indexed search text includes those business fields for retrieval

#### Scenario: Service metadata is excluded
- **WHEN** LD product properties contain metadata such as technical identifiers or system timestamps
- **THEN** those metadata values are not included in the embedded search text

### Requirement: Hybrid TOP-K candidate retrieval
The system SHALL retrieve and retain TOP-K candidates for each non-blank unique query using both semantic and lexical evidence from the LD catalog.

#### Scenario: Hybrid candidates are retained for debugging
- **WHEN** a non-blank query is matched
- **THEN** the debug result contains the ordered candidate list returned by retrieval up to the configured TOP-K limit with available semantic scores, lexical scores, ranks, and fusion scores

#### Scenario: Fewer than TOP-K candidates exist
- **WHEN** retrieval returns fewer candidates than the configured TOP-K limit
- **THEN** the result contains all returned candidates without treating the query as an error

### Requirement: Candidate reranking
The system SHALL rerank retrieved candidates before final product selection so that downstream selection is based on the best available candidate ordering rather than cosine similarity alone.

#### Scenario: Reranked candidates are passed to final selection
- **WHEN** hybrid retrieval returns candidates for a non-blank query
- **THEN** the system provides reranked candidates to the final selection step and retains the reranking evidence for development review

### Requirement: LLM-assisted match decision
The system SHALL use an LLM to evaluate the original query against the reranked candidate set and decide whether one candidate is a suitable LD product match or no suitable product was found.

#### Scenario: LLM selects a suitable candidate
- **WHEN** the LLM determines that one reranked candidate matches the original query
- **THEN** the result status is `MATCHED`, the selected LD product is populated from that candidate, and the development payload records the selected candidate id, confidence when available, and reason

#### Scenario: LLM rejects all candidates
- **WHEN** the LLM determines that none of the reranked candidates suitably matches the original query
- **THEN** the result status is `NOT_FOUND`, the selected LD product is null, and the development payload records the rejection reason when available

#### Scenario: No candidates are returned
- **WHEN** retrieval returns no candidates for a non-blank query
- **THEN** the result status is `NOT_FOUND`, the LD product is null, and the candidate list is empty

### Requirement: Result payload
The system SHALL return structured mapping results containing the original query, status, selected LD product when matched, and retained development details for review.

#### Scenario: Production matched result includes minimal LD product fields
- **WHEN** a query is matched to an LD product
- **THEN** the production-facing selected LD product includes only the available LD name and article

#### Scenario: Development result includes extended LD product fields
- **WHEN** a result is produced for review or debugging
- **THEN** the development payload includes available candidate id, LD id, article, name, DN, PN, joining type, price, URL, properties, retrieval scores, ranks, and LLM selection details

#### Scenario: Not found result has no selected product
- **WHEN** a query is classified as `NOT_FOUND`
- **THEN** the selected LD product is null while retained candidates remain available when retrieval returned them

### Requirement: Duplicate query handling
The system SHALL avoid duplicate retrieval calls for repeated non-blank input strings while preserving one result per original occurrence.

#### Scenario: Repeated query is searched once
- **WHEN** the same non-blank nomenclature string appears multiple times in one input list
- **THEN** the system performs one retrieval for that unique string and returns equivalent mapping results for each original occurrence

### Requirement: RAG quality evaluation
The system SHALL provide an evaluation workflow that measures how well the hybrid retrieval, reranking, and LLM selection pipeline maps reviewed nomenclature examples.

#### Scenario: Evaluation dataset is processed
- **WHEN** an evaluation dataset contains reviewed queries with expected `MATCHED` or `NOT_FOUND` outcomes and acceptable LD product identifiers
- **THEN** the evaluation workflow produces per-query results and aggregate metrics for the configured RAG pipeline

#### Scenario: Retrieval quality is measured separately
- **WHEN** an evaluated query has an expected matched LD product
- **THEN** the evaluation report indicates whether the acceptable LD product appeared in dense, lexical, and hybrid candidate sets at the configured K values

#### Scenario: Final selection quality is measured
- **WHEN** the LLM produces final mapping decisions for evaluated queries
- **THEN** the evaluation report includes final selection accuracy across reviewed `MATCHED` and `NOT_FOUND` examples

#### Scenario: Baseline evaluation does not require a hard quality threshold
- **WHEN** a RAG configuration is evaluated during the MVP baseline phase
- **THEN** the evaluation workflow records metrics, errors, and conclusions without requiring a pass/fail quality threshold

#### Scenario: Error types are classified
- **WHEN** an evaluated query is not handled correctly
- **THEN** the evaluation report classifies the failure as a retrieval miss, wrong LLM selection, wrong `NOT_FOUND`, false match, reranker failure, LLM error, or another explicit reviewed error category

#### Scenario: Wrong not found is reported as the primary business risk
- **WHEN** an evaluated query has an expected matching LD product but the final result is `NOT_FOUND`
- **THEN** the evaluation report identifies the failure as `WRONG_NOT_FOUND` and includes it in a primary business-risk metric

#### Scenario: False match is reported separately
- **WHEN** an evaluated query has expected status `NOT_FOUND` but the final result is `MATCHED`
- **THEN** the evaluation report identifies the failure as `FALSE_MATCH` separately from `WRONG_NOT_FOUND`

### Requirement: Experiment tracking
The system SHALL keep experiment records for RAG quality work so that hypotheses, run settings, results, and conclusions are auditable over time.

#### Scenario: Experiment record captures hypothesis and configuration
- **WHEN** a RAG experiment is recorded
- **THEN** the record includes the hypothesis, dataset reference, retrieval configuration, reranker configuration, Qdrant index configuration, LLM model configuration, system prompt reference, and run timestamp

#### Scenario: Experiment record captures index and prompt versions
- **WHEN** a RAG experiment changes retrieval behavior, Qdrant indexing, or the LLM system prompt
- **THEN** the record identifies the changed search strategy, indexed search text or payload fields, embedding model and dimension, collection or vector configuration, system prompt path or version, and prompt content hash when available

#### Scenario: Experiment record captures results and conclusion
- **WHEN** an evaluation run is attached to an experiment
- **THEN** the record includes aggregate metrics, links or paths to detailed results, observed failure categories, conclusion, and recommended next action

#### Scenario: Experiment records are stored in a stable location
- **WHEN** experiment artifacts are written
- **THEN** they are stored under `data/experiments/` using a naming scheme that allows multiple runs to be compared without overwriting previous results

### Requirement: MVP scope exclusions
The system SHALL NOT require tender document metadata, Excel processing, quantities, units, UI integration, structured characteristic extraction, DN/PN hard filters, material compatibility rules, or manual rule-based product compatibility logic to produce MVP mapping results.

#### Scenario: Plain text-only mapping
- **WHEN** the caller provides only a list of nomenclature strings
- **THEN** the system can produce mapping results without tender id, run id, source document, quantity, unit, extraction confidence, or Excel structure
