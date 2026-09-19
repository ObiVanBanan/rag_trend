# BananLoop dogfood: rag_tender

This branch contains only the evaluator bridge needed for the first 2–3 experiment BananLoop campaign.

## Acceptance contract

Primary metric:

- `quality_floor = min(public_hard_pass_rate, hidden_hard_pass_rate)`

Absolute guards:

- public hard-pass >= 0.93
- hidden hard-pass >= 0.93
- public false-match <= 0
- hidden false-match <= 0
- public human-reject <= 0
- hidden human-reject <= 0
- full pytest passes

This is intentionally narrower than the legacy research harness policy. The mostly-unlabeled 783 corpus is not used as an optimization metric here.

## PowerShell launch

From `C:\Users\theso\Desktop\job\rag_tender`:

```powershell
git fetch origin
git switch bananloop/rag-dogfood-v01
git pull --ff-only origin bananloop/rag-dogfood-v01

uv sync --extra test --extra review
uv run pytest -q tests/test_bananloop_eval_bridge.py

# BananLoop private clones do not contain the gitignored .env.
# Copy .env values into this PowerShell process without printing them.
Get-Content .env | ForEach-Object {
  if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
    $name = $matches[1]
    $value = $matches[2].Trim().Trim('"').Trim("'")
    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
  }
}

if (-not $env:OPENAI_API_KEY) { throw "OPENAI_API_KEY is not loaded" }
if (-not $env:DEEPSEEK_API_KEY) { throw "DEEPSEEK_API_KEY is not loaded" }

docker compose up -d qdrant

$holdout = (Resolve-Path "$HOME\rag-private\rag_hidden_holdout.json").Path
$holdoutPosix = $holdout.Replace('\', '/')
$holdoutSha = (Get-FileHash $holdout -Algorithm SHA256).Hash.ToLower()
$evalCmd = 'uv run --extra test python scripts/bananloop_eval.py --holdout "' + $holdoutPosix + '" --holdout-sha256 ' + $holdoutSha
$state = "$HOME\bananloop-state\rag-tender-night1"

Remove-Item -Recurse -Force $state -ErrorAction SilentlyContinue
```

Then from `C:\Users\theso\Desktop\job\BananLoop`:

```powershell
git switch main
git pull --ff-only origin main
uv sync --extra test
uv run pytest -q

uv run bananloop init `
  --repo ..\rag_tender `
  --state-dir $state `
  --goal "Improve labeled RAG matching quality across public and hidden validation without increasing false matches or human-rejected matches. Prefer general improvements to query interpretation, constraints, retrieval, or reranking; do not weaken technical compatibility or game evaluator cases." `
  --eval-cmd $evalCmd `
  --eval-asset scripts/bananloop_eval.py `
  --eval-asset scripts/eval_harness_gold.py `
  --eval-asset harness_rag/hook.py `
  --eval-asset src/nomenclature_matcher/harness_gold.py `
  --eval-asset src/nomenclature_matcher/golden_rules.py `
  --eval-asset data/harness_gold_combined.json `
  --eval-asset ld_products_full_nomenclature.csv `
  --metric quality_floor `
  --direction maximize `
  --target 1.0 `
  --min-improvement 0.01 `
  --unit ratio `
  --metric-min 0 `
  --metric-max 1 `
  --guard "public_hard_pass_rate>=0.93" `
  --guard "hidden_hard_pass_rate>=0.93" `
  --guard "public_false_match_rate<=0" `
  --guard "hidden_false_match_rate<=0" `
  --guard "public_human_reject_rate<=0" `
  --guard "hidden_human_reject_rate<=0" `
  --check tests `
  --editable src/nomenclature_matcher/query_interpreter.py `
  --editable src/nomenclature_matcher/query_constraints.py `
  --editable src/nomenclature_matcher/query_canonicalization.py `
  --editable src/nomenclature_matcher/query_signals.py `
  --editable src/nomenclature_matcher/matcher.py `
  --editable src/nomenclature_matcher/reranker.py `
  --editable src/nomenclature_matcher/competitor_lookup.py `
  --editable src/nomenclature_matcher/hybrid_retriever.py `
  --editable src/nomenclature_matcher/prompts/query_interpreter_system.md `
  --editable src/nomenclature_matcher/prompts/query_constraints_system.md `
  --editable src/nomenclature_matcher/prompts/reranker_system.md `
  --protect harness_rag/** `
  --protect scripts/** `
  --protect tests/** `
  --protect data/** `
  --protect pyproject.toml `
  --protect uv.lock `
  --agent pi `
  --provider ollama-cloud `
  --model "glm-5.3-flash" `
  --thinking off `
  --max-experiments 3 `
  --max-attempts 5 `
  --max-agent-calls 8 `
  --max-evaluator-calls 4 `
  --deadline-seconds 43200 `
  --plateau 3

uv run bananloop run `
  --repo ..\rag_tender `
  --state-dir $state
```

After completion:

```powershell
uv run bananloop report --repo ..\rag_tender --state-dir $state
uv run bananloop status --repo ..\rag_tender --state-dir $state
```

The original `rag_tender` checkout must stay unchanged. Accepted candidates live only in the BananLoop managed workspace/champion history.
