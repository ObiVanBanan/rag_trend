from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .runtime import HarnessError, agent_env, write_json


def run_codex_agent(
    *,
    project_root: Path,
    run_dir: Path,
    role: str,
    prompt: str,
    schema: dict[str, Any],
    model: str,
    effort: str,
    sandbox: str,
    network: bool,
) -> dict[str, Any]:
    role_dir = run_dir / role
    role_dir.mkdir(parents=True, exist_ok=True)
    schema_path = role_dir / "schema.json"
    result_path = role_dir / "result.json"
    log_path = role_dir / "codex.log"
    write_json(schema_path, schema)
    result_path.unlink(missing_ok=True)

    args = [
        "codex",
        "exec",
        "-m",
        model,
        "--sandbox",
        sandbox,
        "--ephemeral",
        "--color",
        "never",
        "--config",
        'approval_policy="never"',
        "--config",
        f'model_reasoning_effort="{effort}"',
    ]
    if network and sandbox == "workspace-write":
        args += ["--config", "sandbox_workspace_write.network_access=true"]
    args += ["--output-schema", str(schema_path), "--output-last-message", str(result_path), "-"]

    print(f"\n=== {role.upper()} | {model} | reasoning={effort} ===", flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(
            args,
            cwd=project_root,
            text=True,
            input=prompt,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=agent_env(),
        )
    if result.returncode != 0:
        raise HarnessError(f"{role} Codex exited {result.returncode}; see {log_path}")
    if not result_path.exists():
        raise HarnessError(f"{role} did not produce structured result; see {log_path}")
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HarnessError(f"{role} produced invalid structured result: {exc}") from exc
    if not isinstance(payload, dict):
        raise HarnessError(f"{role} result must be a JSON object")
    return payload


def role_settings(config: dict[str, Any], role: str) -> tuple[str, str, bool]:
    agents = dict(config.get("agents") or {})
    defaults = dict(agents.get("defaults") or {})
    specific = dict(agents.get(role) or {})
    model = str(specific.get("model") or defaults.get("model") or "gpt-5.5")
    effort = str(specific.get("reasoning_effort") or defaults.get("reasoning_effort") or "medium")
    network = bool(specific.get("network", defaults.get("network", False)))
    return model, effort, network
