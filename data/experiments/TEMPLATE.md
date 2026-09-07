# Experiment: <YYYY-MM-DD-short-name>

## Hypothesis

<What this run is testing. Prefer one primary variable per experiment.>

## Dataset

- Queries: <path>
- Labels: <path>
- Review status: <summary>

## Retrieval Configuration

- Dense limit: <value>
- BM25 limit: <value>
- Hybrid/rerank limit: <value>
- RRF k: <value>

## Qdrant Index Configuration

- Collection alias: <value>
- Vector name: <value>
- Embedding model: <value>
- Embedding dimension: <value>
- Search text fields: <fields>
- Payload fields: <fields>
- Rebuild source/date: <source/date>

## Reranker And LLM

- Reranker: <name/config>
- LLM model: <name>
- System prompt path: <path>
- System prompt hash: <sha256>

## Metrics

- Dense Recall@K: <value>
- BM25 Recall@K: <value>
- Hybrid Recall@K: <value>
- Final selection accuracy: <value>
- WRONG_NOT_FOUND rate: <value>
- FALSE_MATCH rate: <value>
- Wrong product selection rate: <value>

## Error Breakdown

<Counts and notable examples.>

## Results

- Detailed results: <path>

## Conclusion

<accept / reject / next experiment, with rationale>

## Next Action

<Concrete follow-up>
