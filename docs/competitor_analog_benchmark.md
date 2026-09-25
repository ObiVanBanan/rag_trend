# Competitor analogue hit-any benchmark

Source rows pair one LD ball valve with one competitor analogue.

Evaluation semantics:

1. Group source rows by normalized competitor product name.
2. Use the competitor product name as the RAG query.
3. Collect every LD product linked to that competitor as `acceptable_ld_ids`.
4. Score **PASS** when the top-1 returned LD id belongs to that set.
5. Do **not** require the service to recover every acceptable analogue.

This intentionally measures practical substitute retrieval rather than completeness.

## Frozen benchmark bundled with this branch

The branch contains the grouped benchmark as an xz-compressed UTF-8 TSV,
base64-split into six text parts under:

`data/competitor_analog_hit_any_min_v1.parts/`

The evaluator reconstructs it automatically and verifies both the compressed
and decompressed SHA-256 before any RAG/API calls. No external mapping file is
required for normal evaluation.

Dataset summary:

- 55,539 source mappings
- 15,885 grouped competitor queries
- 3,279 unique acceptable LD ids
- 2.762 average acceptable LD ids per query
- 23 maximum acceptable LD ids for one query

Verify the frozen dataset without calling the matcher:

```powershell
uv run python scripts/eval_competitor_analogs.py --verify-only
```

Representative deterministic sample:

```powershell
uv run python scripts/eval_competitor_analogs.py `
  --sample 1000 `
  --seed 42 `
  --workers 1 `
  --output ".tmp\champion_competitor_1000.json"
```

Full run:

```powershell
uv run python scripts/eval_competitor_analogs.py `
  --workers 1 `
  --output ".tmp\champion_competitor_full.json"
```

For audit/re-generation, the original raw source can still be supplied:

```powershell
uv run python scripts/eval_competitor_analogs.py `
  --mapping "$HOME\rag-private\mapping_results.csv" `
  --workers 1
```
