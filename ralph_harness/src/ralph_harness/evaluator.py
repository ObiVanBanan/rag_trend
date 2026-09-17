from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .runtime import HarnessError, run


def _render_command(command: list[str], *, output: Path, project_root: Path) -> list[str]:
    # Replace only the harness placeholders. Using str.format() here would also
    # interpret unrelated braces in inline Python/JSON/shell snippets.
    return [
        str(part)
        .replace("{output}", str(output))
        .replace("{project_root}", str(project_root))
        for part in command
    ]


def run_evaluator(
    spec: dict[str, Any],
    *,
    project_root: Path,
    output_dir: Path,
    label: str,
) -> dict[str, Any]:
    name = str(spec["name"])
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{label}.{name}.json"
    log_path = output_dir / f"{label}.{name}.log"
    command = _render_command(list(spec["command"]), output=output, project_root=project_root)
    env = os.environ.copy()
    env.update({str(k): str(v) for k, v in dict(spec.get("env") or {}).items()})
    result = run(command, root=project_root, check=False, env=env, log_path=log_path)
    if result.returncode != 0:
        raise HarnessError(f"evaluator {name!r} failed ({result.returncode}); see {log_path}")
    if not output.exists():
        raise HarnessError(
            f"evaluator {name!r} did not create {output}; its command must write JSON to {{output}}"
        )
    try:
        payload = json.loads(output.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HarnessError(f"evaluator {name!r} produced invalid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise HarnessError(f"evaluator {name!r} output must be a JSON object")
    metrics = payload.get("metrics")
    if metrics is None:
        metrics = {k: v for k, v in payload.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    if not isinstance(metrics, dict):
        raise HarnessError(f"evaluator {name!r} metrics must be a JSON object")
    numeric: dict[str, float] = {}
    for key, value in metrics.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        numeric[str(key)] = float(value)
    return {
        "name": name,
        "kind": str(spec.get("kind") or "gate"),
        "metrics": numeric,
        "payload": payload,
        "output": str(output),
        "log": str(log_path),
    }


def metric_delta(champion: dict[str, Any], candidate: dict[str, Any]) -> dict[str, float]:
    before = dict(champion.get("metrics") or {})
    after = dict(candidate.get("metrics") or {})
    return {
        key: float(after[key]) - float(before[key])
        for key in sorted(set(before) & set(after))
    }


def gate_candidate(
    spec: dict[str, Any],
    *,
    champion: dict[str, Any],
    candidate: dict[str, Any],
) -> tuple[bool, list[str], dict[str, float]]:
    policy = dict(spec.get("policy") or {})
    delta = metric_delta(champion, candidate)
    before = dict(champion.get("metrics") or {})
    after = dict(candidate.get("metrics") or {})
    reasons: list[str] = []

    primary = dict(policy.get("primary") or {})
    if primary:
        metric = str(primary["metric"])
        direction = str(primary.get("direction") or "maximize")
        min_delta = float(primary.get("min_delta") or 0.0)
        if metric not in before or metric not in after:
            reasons.append(f"missing primary metric {metric!r}")
        else:
            change = float(after[metric]) - float(before[metric])
            if direction == "maximize" and change + 1e-12 < min_delta:
                reasons.append(f"primary metric {metric} improved by {change:.6g}, required >= {min_delta:.6g}")
            elif direction == "minimize" and -change + 1e-12 < min_delta:
                reasons.append(f"primary metric {metric} decreased by {-change:.6g}, required >= {min_delta:.6g}")
            elif direction not in {"maximize", "minimize"}:
                reasons.append(f"unsupported direction {direction!r} for {metric}")

    for guardrail in policy.get("guardrails") or []:
        item = dict(guardrail)
        metric = str(item["metric"])
        direction = str(item.get("direction") or "maximize")
        max_regression = float(item.get("max_regression") or 0.0)
        if metric not in before or metric not in after:
            reasons.append(f"missing guardrail metric {metric!r}")
            continue
        regression = (
            float(before[metric]) - float(after[metric])
            if direction == "maximize"
            else float(after[metric]) - float(before[metric])
        )
        if direction not in {"maximize", "minimize"}:
            reasons.append(f"unsupported direction {direction!r} for {metric}")
        elif regression > max_regression + 1e-12:
            reasons.append(
                f"guardrail {metric} regressed by {regression:.6g}, allowed <= {max_regression:.6g}"
            )

    return not reasons, reasons, delta
