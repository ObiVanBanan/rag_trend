# Competitor analogue hit-any benchmark

Source rows pair one LD ball valve with one competitor analogue.

Evaluation semantics:

1. Group source rows by normalized competitor product name.
2. Use the competitor product name as the RAG query.
3. Collect every LD product linked to that competitor as `acceptable_ld_ids`.
4. Score **PASS** when the top-1 returned LD id belongs to that set.
5. Do **not** require the service to recover every acceptable analogue.

This intentionally measures practical substitute retrieval rather than completeness.

Run:

```powershell
uv run python scripts/eval_competitor_analogs.py `
  --mapping "$HOME\rag-private\mapping_results.csv" `
  --workers 1 `
  --output ".tmp\competitor_analog_eval.json"
```

The source mapping CSV is intentionally kept outside git; the evaluator groups it at runtime.
