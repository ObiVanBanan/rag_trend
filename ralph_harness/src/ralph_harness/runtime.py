from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any


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
    root: Path,
    check: bool = True,
    env: dict[str, str] | None = None,
    input_text: str | None = None,
    log_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    print("$ " + " ".join(shlex.quote(str(arg)) for arg in args), flush=True)
    result = subprocess.run(
        [str(arg) for arg in args],
        cwd=root,
        text=True,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    output = result.stdout or ""
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(output, encoding="utf-8")
    if output:
        print(output, end="" if output.endswith("\n") else "\n")
    if check and result.returncode != 0:
        raise HarnessError(f"command failed ({result.returncode}): {' '.join(args)}")
    return result


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], root=root, check=check)


def head(root: Path) -> str:
    return git(root, "rev-parse", "HEAD").stdout.strip()


def branch(root: Path) -> str:
    return git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()


def clean(root: Path) -> bool:
    return not git(root, "status", "--porcelain").stdout.strip()


def changed_paths(root: Path) -> set[str]:
    tracked = git(root, "diff", "--name-only", "HEAD", "--").stdout.splitlines()
    untracked = git(root, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    return {p.strip().replace("\\", "/") for p in [*tracked, *untracked] if p.strip()}


def protected_changes(root: Path, protected_paths: list[str]) -> list[str]:
    normalized = [p.replace("\\", "/").rstrip("/") for p in protected_paths]
    found: list[str] = []
    for path in sorted(changed_paths(root)):
        for protected in normalized:
            if path == protected or path.startswith(protected + "/"):
                found.append(path)
                break
    return found


def rollback(root: Path, commit: str) -> None:
    print(f"Rolling back to champion {commit[:12]}", flush=True)
    git(root, "reset", "--hard", commit)
    git(root, "clean", "-fd")


def commit_all(root: Path, message: str) -> str:
    git(root, "add", "-A")
    git(root, "commit", "-m", message)
    return head(root)


def push(root: Path) -> bool:
    result = git(root, "push", "origin", branch(root), check=False)
    if result.returncode != 0:
        print("WARNING: push failed; accepted champion is kept locally.", flush=True)
        return False
    return True


def agent_env() -> dict[str, str]:
    env = os.environ.copy()
    env["RALPH_HARNESS_AGENT"] = "1"
    return env
