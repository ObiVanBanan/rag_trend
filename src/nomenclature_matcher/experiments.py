from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXPERIMENT_TEMPLATE = """# Experiment: {name}

## Hypothesis

{hypothesis}

## Dataset

- Path: {dataset_path}
- Labels: {labels_path}

## Configuration

```json
{configuration_json}
```

## Metrics

```json
{metrics_json}
```

## Results

- Detailed results: {results_path}

## Conclusion

{conclusion}

## Next Action

{next_action}
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def file_sha256(path: str | Path) -> str | None:
    file_path = Path(path)
    if not file_path.exists() or not file_path.is_file():
        return None
    return hashlib.sha256(file_path.read_bytes()).hexdigest()


def settings_snapshot(settings: Any, *, prompt_path: str | Path | None = None) -> dict[str, Any]:
    prompt_hash = file_sha256(prompt_path) if prompt_path else None
    return {
        "retrieval": {
            "hybrid_dense_limit": getattr(settings, "hybrid_dense_limit", None),
            "hybrid_bm25_limit": getattr(settings, "hybrid_bm25_limit", None),
            "hybrid_rerank_limit": getattr(settings, "hybrid_rerank_limit", None),
            "rrf_k": getattr(settings, "rrf_k", None),
            "rerank_result_limit": getattr(settings, "rerank_result_limit", None),
        },
        "qdrant_index": {
            "collection_alias": getattr(settings, "qdrant_collection_alias", None),
            "dense_vector_name": getattr(settings, "qdrant_dense_vector_name", None),
            "embedding_model": getattr(settings, "embedding_model", None),
            "embedding_dimension": getattr(settings, "embedding_dimension", None),
            "search_text_fields": ["name", "article", "dn", "pn", "joining_type", "properties_json"],
            "payload_fields": ["ld_id", "name", "article", "price", "dn", "pn", "joining_type", "url", "properties", "search_text"],
        },
        "llm": {
            "model": getattr(settings, "deepseek_model", None),
            "base_url": getattr(settings, "deepseek_base_url", None),
            "timeout_seconds": getattr(settings, "deepseek_timeout_seconds", None),
            "temperature": 0,
        },
        "system_prompt": {
            "path": str(prompt_path) if prompt_path else None,
            "sha256": prompt_hash,
        },
    }


def create_experiment_record(
    experiments_dir: str | Path,
    run_name: str,
    *,
    hypothesis: str,
    dataset_path: str | Path,
    labels_path: str | Path,
    configuration: dict[str, Any],
    metrics: dict[str, Any],
    results: dict[str, Any],
    conclusion: str,
    next_action: str,
) -> Path:
    root = Path(experiments_dir)
    run_dir = root / run_name
    if run_dir.exists():
        raise FileExistsError(f"Experiment run already exists: {run_dir}")
    run_dir.mkdir(parents=True)
    results_path = run_dir / "results.json"
    experiment_path = run_dir / "experiment.md"
    result_payload = {
        "generated_at": utc_now_iso(),
        "run_name": run_name,
        "hypothesis": hypothesis,
        "dataset_path": str(dataset_path),
        "labels_path": str(labels_path),
        "configuration": configuration,
        "metrics": metrics,
        "results": results.get("results", results),
    }
    results_path.write_text(json.dumps(result_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    experiment_path.write_text(
        EXPERIMENT_TEMPLATE.format(
            name=run_name,
            hypothesis=hypothesis,
            dataset_path=dataset_path,
            labels_path=labels_path,
            configuration_json=json.dumps(configuration, ensure_ascii=False, indent=2),
            metrics_json=json.dumps(metrics, ensure_ascii=False, indent=2),
            results_path=results_path.name,
            conclusion=conclusion,
            next_action=next_action,
        ),
        encoding="utf-8",
    )
    return run_dir
