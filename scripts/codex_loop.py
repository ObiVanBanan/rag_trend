#!/usr/bin/env python3
"""Autonomous Codex worker for a dedicated clone of rag_trend.

The worker polls GitHub, executes a selected Markdown plan once per content hash,
verifies the repository, commits, and pushes back to the configured branch.

Recommended usage (dedicated clone, not your normal working tree):

    python scripts/codex_loop.py --plan CODEX_PLAN.md

Useful options:

    --interval 300          poll every 5 minutes
    --once                  execute one polling iteration and exit
    --timeout 3600          maximum Codex runtime per attempt
    --verify "python -m pytest -q"
    --allow-network         enable network inside Codex workspace-write sandbox

Safety notes:
- Git operations are performed by this wrapper, not by Codex.
- Codex is run with workspace-write sandbox and approval_policy=never.
- Network is disabled by default.
- Service credentials are excluded from commands spawned by Codex.
- A non-empty .env containing API keys is rejected by default. Use a dedicated
  worker clone with gateway/read-only credentials, or explicitly opt out with
  --allow-project-secrets.
- The same plan content is not executed twice after a successful push.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


DEFAULT_BRANCH = "main"
DEFAULT_INTERVAL = 300
DEFAULT_TIMEOUT = 3600
DEFAULT_MAX_FAILURES = 3
DEFAULT_MAX_CHANGED_FILES = 50
DEFAULT_MAX_DIFF_LINES = 5000
DEFAULT_VERIFY = "python -m pytest -q"

# These variables may be needed by Codex itself, but should not automatically
# leak into shell commands that Codex runs inside the repository.
SERVICE_ENV_PATTERNS = [
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "QDRANT_API_KEY",
    "QDRANT_URL",
]

SENSITIVE_ENV_KEYS = {
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "QDRANT_API_KEY",
}


@dataclass(frozen=True)
class Config:
    repo: Path
    plan: str
    branch: str
    interval: int
    timeout: int
    max_failures: int
    max_changed_files: int
    max_diff_lines: int
    verify: str
    once: bool
    allow_network: bool
    allow_project_secrets: bool

    @property
    def git_dir(self) -> Path:
        return self.repo / ".git"

    @property
    def runtime_dir(self) -> Path:
        return self.git_dir / "codex-loop"

    @property
    def state_file(self) -> Path:
        return self.runtime_dir / "state.json"

    @property
    def schema_file(self) -> Path:
        return self.runtime_dir / "result.schema.json"

    @property
    def result_file(self) -> Path:
        return self.runtime_dir / "result.json"

    @property
    def log_file(self) -> Path:
        return self.runtime_dir / "codex.log"

    @property
    def lock_file(self) -> Path:
        return self.runtime_dir / "worker.lock"


class LoopError(RuntimeError):
    pass


def log(message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] {message}", flush=True)


def run(
    cfg: Config,
    args: list[str],
    *,
    check: bool = True,
    capture: bool = True,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    log("$ " + " ".join(args))
    result = subprocess.run(
        args,
        cwd=cfg.repo,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        raise LoopError(
            f"command failed ({result.returncode}): {' '.join(args)}\n"
            f"{result.stdout or ''}"
        )
    return result


def git(cfg: Config, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(cfg, ["git", *args], check=check)


def load_state(cfg: Config) -> dict:
    if not cfg.state_file.exists():
        return {}
    try:
        return json.loads(cfg.state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(cfg: Config, state: dict) -> None:
    cfg.runtime_dir.mkdir(parents=True, exist_ok=True)
    tmp = cfg.state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cfg.state_file)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repo_is_clean(cfg: Config) -> bool:
    return not git(cfg, "status", "--porcelain").stdout.strip()


def reset_worker_changes(cfg: Config) -> None:
    log("discarding worker changes")
    git(cfg, "reset", "--hard", "HEAD", check=False)
    git(cfg, "clean", "-fd", check=False)


def parse_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def assert_project_secrets_safe(cfg: Config) -> None:
    if cfg.allow_project_secrets:
        return
    dotenv = parse_dotenv(cfg.repo / ".env")
    exposed = sorted(key for key in SENSITIVE_ENV_KEYS if dotenv.get(key))
    if exposed:
        raise LoopError(
            "refusing to start Codex because .env contains non-empty service secrets: "
            + ", ".join(exposed)
            + ". Use a dedicated worker clone with limited gateway/read-only credentials. "
            "If you intentionally accept this risk, pass --allow-project-secrets."
        )


def write_result_schema(cfg: Config) -> None:
    schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["complete", "blocked"]},
            "summary": {"type": "string"},
            "tests": {"type": "string"},
            "blocker": {"type": "string"},
        },
        "required": ["status", "summary", "tests", "blocker"],
        "additionalProperties": False,
    }
    cfg.schema_file.write_text(json.dumps(schema, indent=2), encoding="utf-8")


def codex_prompt(cfg: Config) -> str:
    return f"""You are an autonomous software-engineering worker in a dedicated clone.

Read `{cfg.plan}` completely before making changes and execute that plan.

Rules:
1. Inspect the existing implementation before editing.
2. Implement the entire plan; keep changes scoped to it.
3. Run relevant tests/checks and fix failures caused by your changes.
4. Do not run git pull, git fetch, git push, git commit, git reset, git clean,
   git checkout, git switch, git rebase, or modify Git refs. The outer harness
   owns all Git synchronization and publishing.
5. Do not modify `{cfg.plan}` or `scripts/codex_loop.py`.
6. Do not ask questions. Make conservative implementation decisions yourself.
7. Do not claim completion when required work is unfinished or tests are failing.
8. Do not attempt to bypass sandbox, network, credential, proxy, rate-limit, or
   budget restrictions. If a required dependency is inaccessible, return blocked.

Return status `complete` only when the plan is fully implemented. Return
`blocked` when safe completion is impossible and explain the blocker.
"""


def codex_args(cfg: Config) -> list[str]:
    # shell_environment_policy limits what repository commands launched by Codex
    # inherit. It does not need to remove credentials used internally by the
    # Codex CLI itself.
    excluded = json.dumps(SERVICE_ENV_PATTERNS, separators=(",", ":"))
    args = [
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "--ephemeral",
        "--color",
        "never",
        "--config",
        "approval_policy=\"never\"",
        "--config",
        f"shell_environment_policy.exclude={excluded}",
    ]
    if cfg.allow_network:
        args.extend(
            ["--config", "sandbox_workspace_write.network_access=true"]
        )
    args.extend(
        [
            "--output-schema",
            str(cfg.schema_file),
            "--output-last-message",
            str(cfg.result_file),
            "-",
        ]
    )
    return args


def run_codex(cfg: Config) -> dict:
    cfg.result_file.unlink(missing_ok=True)
    assert_project_secrets_safe(cfg)

    # Preserve the ignored local .env exactly; Codex is not allowed to rewrite it.
    dotenv_path = cfg.repo / ".env"
    dotenv_before = dotenv_path.read_bytes() if dotenv_path.exists() else None

    log("starting Codex")
    try:
        with cfg.log_file.open("w", encoding="utf-8") as logfile:
            result = subprocess.run(
                codex_args(cfg),
                cwd=cfg.repo,
                text=True,
                input=codex_prompt(cfg),
                stdout=logfile,
                stderr=subprocess.STDOUT,
                timeout=cfg.timeout,
            )
    finally:
        if dotenv_before is not None:
            dotenv_path.write_bytes(dotenv_before)
        elif dotenv_path.exists():
            dotenv_path.unlink()

    if result.returncode != 0:
        raise LoopError(
            f"Codex exited with code {result.returncode}; see {cfg.log_file}"
        )
    if not cfg.result_file.exists():
        raise LoopError("Codex did not produce a structured result")
    try:
        return json.loads(cfg.result_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LoopError(f"invalid Codex result JSON: {exc}") from exc


def restore_protected_files(cfg: Config) -> None:
    for path in (cfg.plan, "scripts/codex_loop.py"):
        result = git(cfg, "ls-files", "--error-unmatch", path, check=False)
        if result.returncode == 0:
            git(cfg, "restore", "--source=HEAD", "--", path)


def diff_stats(cfg: Config) -> tuple[int, int]:
    output = git(cfg, "diff", "--numstat").stdout
    files = 0
    lines = 0
    for row in output.splitlines():
        parts = row.split("\t", 2)
        if len(parts) != 3:
            continue
        files += 1
        added, removed = parts[0], parts[1]
        if added.isdigit():
            lines += int(added)
        if removed.isdigit():
            lines += int(removed)
    return files, lines


def enforce_diff_budget(cfg: Config) -> None:
    files, lines = diff_stats(cfg)
    log(f"diff budget: files={files}/{cfg.max_changed_files}, lines={lines}/{cfg.max_diff_lines}")
    if files > cfg.max_changed_files:
        raise LoopError(f"changed-file budget exceeded: {files} > {cfg.max_changed_files}")
    if lines > cfg.max_diff_lines:
        raise LoopError(f"diff-line budget exceeded: {lines} > {cfg.max_diff_lines}")


def verify(cfg: Config) -> None:
    if not cfg.verify.strip():
        log("outer verification disabled")
        return
    command = shlex.split(cfg.verify, posix=os.name != "nt")
    result = run(cfg, command, check=False, timeout=cfg.timeout)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode != 0:
        raise LoopError(f"verification failed with exit code {result.returncode}")


def register_failure(cfg: Config, state: dict, plan_hash: str, error: str) -> None:
    if state.get("failing_plan_hash") == plan_hash:
        count = int(state.get("failure_count", 0)) + 1
    else:
        count = 1
    state["failing_plan_hash"] = plan_hash
    state["failure_count"] = count
    state["last_error"] = error
    state["last_failure_at"] = datetime.now().isoformat(timespec="seconds")
    if count >= cfg.max_failures:
        state["blocked_plan_hash"] = plan_hash
        log(f"plan failed {count} times; marking this plan hash BLOCKED")
    save_state(cfg, state)


def iteration(cfg: Config) -> None:
    state = load_state(cfg)

    if not repo_is_clean(cfg):
        log("working tree is dirty; skipping to avoid overwriting manual work")
        return

    git(cfg, "checkout", cfg.branch)
    git(cfg, "pull", "--ff-only", "origin", cfg.branch)

    plan_path = cfg.repo / cfg.plan
    if not plan_path.exists():
        log(f"plan file {cfg.plan!r} does not exist; nothing to do")
        return

    plan_hash = sha256(plan_path)
    if state.get("completed_plan_hash") == plan_hash:
        log(f"plan {plan_hash[:8]} already completed; waiting for plan change")
        return
    if state.get("blocked_plan_hash") == plan_hash:
        log(f"plan {plan_hash[:8]} is BLOCKED; waiting for plan change")
        return

    base_commit = git(cfg, "rev-parse", "HEAD").stdout.strip()

    try:
        result = run_codex(cfg)
        restore_protected_files(cfg)

        if result.get("status") != "complete":
            blocker = result.get("blocker") or "Codex reported blocked"
            log(f"Codex BLOCKED: {blocker}")
            reset_worker_changes(cfg)
            state["blocked_plan_hash"] = plan_hash
            state["last_result"] = result
            state["failure_count"] = 0
            save_state(cfg, state)
            return

        if repo_is_clean(cfg):
            log("Codex reports complete and repository already needs no changes")
            state["completed_plan_hash"] = plan_hash
            state["blocked_plan_hash"] = None
            state["failure_count"] = 0
            state["last_result"] = result
            save_state(cfg, state)
            return

        enforce_diff_budget(cfg)
        verify(cfg)

        # If anyone pushed while Codex was working, discard this attempt and retry
        # against the new branch head on the next iteration. No autonomous rebase.
        git(cfg, "fetch", "origin", cfg.branch)
        remote_commit = git(cfg, "rev-parse", f"origin/{cfg.branch}").stdout.strip()
        if remote_commit != base_commit:
            log("remote changed during Codex run; discarding attempt and retrying later")
            git(cfg, "reset", "--hard", f"origin/{cfg.branch}")
            git(cfg, "clean", "-fd", check=False)
            return

        git(cfg, "add", "-A")
        git(cfg, "commit", "-m", f"codex-loop: execute {cfg.plan} {plan_hash[:8]}")
        git(cfg, "push", "origin", cfg.branch)

        commit = git(cfg, "rev-parse", "HEAD").stdout.strip()
        state["completed_plan_hash"] = plan_hash
        state["blocked_plan_hash"] = None
        state["failing_plan_hash"] = None
        state["failure_count"] = 0
        state["last_result"] = result
        state["completed_commit"] = commit
        state["completed_at"] = datetime.now().isoformat(timespec="seconds")
        save_state(cfg, state)
        log(f"plan completed and pushed: {commit[:12]} ✅")

    except subprocess.TimeoutExpired:
        log("Codex/verification timed out")
        reset_worker_changes(cfg)
        register_failure(cfg, state, plan_hash, "timeout")
    except Exception as exc:
        log(f"iteration failed: {exc}")
        reset_worker_changes(cfg)
        register_failure(cfg, state, plan_hash, str(exc))


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_lock(cfg: Config) -> None:
    cfg.runtime_dir.mkdir(parents=True, exist_ok=True)
    if cfg.lock_file.exists():
        try:
            old_pid = int(cfg.lock_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            old_pid = -1
        if pid_alive(old_pid):
            raise LoopError(f"another codex loop is already running (pid={old_pid})")
        cfg.lock_file.unlink(missing_ok=True)
    cfg.lock_file.write_text(str(os.getpid()), encoding="utf-8")


def release_lock(cfg: Config) -> None:
    cfg.lock_file.unlink(missing_ok=True)


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Poll a Markdown plan and execute it with Codex.")
    parser.add_argument("--repo", default=".", help="repository path (default: current directory)")
    parser.add_argument("--plan", required=True, help="Markdown plan path relative to repository root")
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--max-failures", type=int, default=DEFAULT_MAX_FAILURES)
    parser.add_argument("--max-changed-files", type=int, default=DEFAULT_MAX_CHANGED_FILES)
    parser.add_argument("--max-diff-lines", type=int, default=DEFAULT_MAX_DIFF_LINES)
    parser.add_argument("--verify", default=DEFAULT_VERIFY, help="outer verification command; empty disables")
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="allow network in Codex sandbox; only use behind an OS/container allowlist or budget proxy",
    )
    parser.add_argument(
        "--allow-project-secrets",
        action="store_true",
        help="allow non-empty API keys in .env (unsafe for unattended workers)",
    )
    args = parser.parse_args()
    return Config(
        repo=Path(args.repo).resolve(),
        plan=args.plan,
        branch=args.branch,
        interval=max(1, args.interval),
        timeout=max(1, args.timeout),
        max_failures=max(1, args.max_failures),
        max_changed_files=max(1, args.max_changed_files),
        max_diff_lines=max(1, args.max_diff_lines),
        verify=args.verify,
        once=args.once,
        allow_network=args.allow_network,
        allow_project_secrets=args.allow_project_secrets,
    )


def main() -> int:
    cfg = parse_args()
    if not cfg.git_dir.is_dir():
        print(f"not a git repository: {cfg.repo}", file=sys.stderr)
        return 2

    cfg.runtime_dir.mkdir(parents=True, exist_ok=True)
    write_result_schema(cfg)

    try:
        acquire_lock(cfg)
    except LoopError as exc:
        print(exc, file=sys.stderr)
        return 2

    log(f"repo={cfg.repo}")
    log(f"branch={cfg.branch} plan={cfg.plan} interval={cfg.interval}s")
    log(f"network={'enabled' if cfg.allow_network else 'disabled'}")

    try:
        while True:
            iteration(cfg)
            if cfg.once:
                return 0
            log(f"sleeping {cfg.interval}s")
            time.sleep(cfg.interval)
    except KeyboardInterrupt:
        log("stopped by user")
        return 130
    finally:
        release_lock(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
