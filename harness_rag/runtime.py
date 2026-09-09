from __future__ import annotations

import json
import os
import shlex
import subprocess
import uuid
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

PROTECTED_EXACT = {
    "ld_products_full_nomenclature.csv",
    "scripts/build_index.py",
    "scripts/eval_harness_gold.py",
    "src/nomenclature_matcher/golden_rules.py",
    "src/nomenclature_matcher/harness_gold.py",
}
PROTECTED_PREFIXES = ("harness_rag/", "data/")


class HarnessError(RuntimeError):
    pass


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def run(
    args: list[str],
    *,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    print("$ " + " ".join(shlex.quote(arg) for arg in args), flush=True)
    result = subprocess.run(
        args,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if check and result.returncode != 0:
        raise HarnessError(f"command failed ({result.returncode}): {' '.join(args)}")
    return result


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    # A remote push is publication, not part of the scientific acceptance gate.
    # Never discard/abort an accepted local champion only because GitHub auth or
    # connectivity is temporarily unavailable. Callers still receive the
    # non-zero return code and the command output is printed by run().
    if args and args[0] == "push" and check:
        result = run(["git", *args], check=False)
        if result.returncode != 0:
            print(
                "WARNING: git push failed; keeping the accepted champion locally. "
                "Fix GitHub authentication and push later.",
                flush=True,
            )
        return result
    return run(["git", *args], check=check)


def head() -> str:
    return git("rev-parse", "HEAD").stdout.strip()


def branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def clean() -> bool:
    return not git("status", "--porcelain").stdout.strip()


def changed_paths() -> set[str]:
    tracked = git("diff", "--name-only", "HEAD", "--").stdout.splitlines()
    untracked = git("ls-files", "--others", "--exclude-standard").stdout.splitlines()
    return {p.strip().replace("\\", "/") for p in [*tracked, *untracked] if p.strip()}


def protected_changes() -> list[str]:
    found: list[str] = []
    for path in sorted(changed_paths()):
        if path in PROTECTED_EXACT or any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES):
            found.append(path)
    return found


def planner_changes_are_scoped() -> bool:
    paths = changed_paths()
    return bool(paths) and all(path.startswith("openspec/changes/") for path in paths)


def rollback(commit: str) -> None:
    print(f"Rolling back to champion {commit[:12]}", flush=True)
    git("reset", "--hard", commit)
    git("clean", "-fd")


def ensure_outside_repo(path: Path, label: str) -> None:
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        return
    raise HarnessError(f"{label} must live outside the repository: {path}")


def collection_env(alias: str | None) -> dict[str, str]:
    return {"QDRANT_COLLECTION_ALIAS": alias} if alias else {}


def _agent_env(alias: str | None) -> dict[str, str]:
    env = os.environ.copy()
    env["RAG_HARNESS_AGENT"] = "1"
    if alias:
        env["QDRANT_COLLECTION_ALIAS"] = alias
    for key in list(env):
        if key.startswith("RAG_HARNESS_HOLDOUT") or key.startswith("RAG_HOLDOUT"):
            env.pop(key, None)
    return env


def run_codex(
    *,
    role: str,
    prompt: str,
    schema: dict[str, Any],
    model: str,
    effort: str,
    sandbox: str,
    run_dir: Path,
    network: bool,
    qdrant_alias: str | None,
) -> dict[str, Any]:
    role_dir = run_dir / role
    role_dir.mkdir(parents=True, exist_ok=True)
    schema_path = role_dir / "schema.json"
    result_path = role_dir / "result.json"
    log_path = role_dir / "codex.log"
    write_json(schema_path, schema)
    result_path.unlink(missing_ok=True)

    args = [
        "codex", "exec", "-m", model,
        "--sandbox", sandbox,
        "--ephemeral", "--color", "never",
        "--config", 'approval_policy="never"',
        "--config", f'model_reasoning_effort="{effort}"',
    ]
    if network and sandbox == "workspace-write":
        args += ["--config", "sandbox_workspace_write.network_access=true"]
    args += ["--output-schema", str(schema_path), "--output-last-message", str(result_path), "-"]

    print(f"\n=== {role.upper()} | {model} | reasoning={effort} ===", flush=True)
    with log_path.open("w", encoding="utf-8") as log:
        result = subprocess.run(
            args,
            cwd=ROOT,
            text=True,
            input=prompt,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=_agent_env(qdrant_alias),
        )
    if result.returncode != 0:
        raise HarnessError(f"{role} Codex exited {result.returncode}; see {log_path}")
    if not result_path.exists():
        raise HarnessError(f"{role} did not produce structured result")
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HarnessError(f"{role} result is not a JSON object")
    return payload


def run_tests(config: dict[str, Any], run_dir: Path, tag: str, alias: str | None) -> tuple[bool, str]:
    env = os.environ.copy()
    env.update(collection_env(alias))
    result = run([str(x) for x in config["test_command"]], check=False, env=env)
    text = result.stdout or ""
    (run_dir / f"{tag}_tests.log").write_text(text, encoding="utf-8")
    return result.returncode == 0, text[-12000:]


def rebuild_index(
    config: dict[str, Any],
    state: dict[str, Any],
    run_dir: Path,
    *,
    cycle: int,
    reason: str,
) -> str:
    used = int(state.get("index_builds_used", 0))
    limit = int(config["max_index_builds"])
    if used >= limit:
        raise HarnessError(f"index rebuild budget exhausted ({used}/{limit})")

    alias = f"rag_harness_c{cycle:02d}_b{used + 1}_{uuid.uuid4().hex[:8]}"
    env = os.environ.copy()
    env.pop("RAG_HARNESS_AGENT", None)
    env["QDRANT_COLLECTION_ALIAS"] = alias
    result = run([str(x) for x in config["index_command"]], check=False, env=env)
    (run_dir / f"reindex_{used + 1}.log").write_text(result.stdout or "", encoding="utf-8")
    if result.returncode != 0:
        raise HarnessError("full index rebuild failed")

    state["index_builds_used"] = used + 1
    state.setdefault("index_history", []).append(
        {"reason": reason, "count": used + 1, "collection": alias}
    )
    return alias
