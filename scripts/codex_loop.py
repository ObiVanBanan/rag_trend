#!/usr/bin/env python3
"""Autonomous Codex worker for a dedicated clone of rag_trend.

Typical use:
    python scripts/codex_loop.py --plan CODEX_PLAN.md

The process stays alive, pulls main every 5 minutes by default, executes a
changed Markdown plan once, runs an outer verification command, commits and
pushes the result. State and logs live under .git/codex-loop/.

Run this only in a dedicated worker clone. On failed attempts the script may
run `git reset --hard` and `git clean -fd`.
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


DEFAULT_VERIFY = "python -m pytest -q"
SERVICE_ENV_KEYS = {
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "QDRANT_API_KEY",
    "QDRANT_URL",
}
SENSITIVE_DOTENV_KEYS = {"OPENAI_API_KEY", "DEEPSEEK_API_KEY", "QDRANT_API_KEY"}


class LoopError(RuntimeError):
    pass


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
    def runtime(self) -> Path:
        return self.git_dir / "codex-loop"

    @property
    def state_file(self) -> Path:
        return self.runtime / "state.json"

    @property
    def result_file(self) -> Path:
        return self.runtime / "result.json"

    @property
    def schema_file(self) -> Path:
        return self.runtime / "result.schema.json"

    @property
    def log_file(self) -> Path:
        return self.runtime / "codex.log"

    @property
    def lock_file(self) -> Path:
        return self.runtime / "worker.lock"


def log(message: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}", flush=True)


def command(
    cfg: Config,
    args: list[str],
    *,
    check: bool = True,
    timeout: int | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    log("$ " + " ".join(args))
    result = subprocess.run(
        args,
        cwd=cfg.repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        env=env,
    )
    if check and result.returncode != 0:
        raise LoopError(
            f"command failed ({result.returncode}): {' '.join(args)}\n{result.stdout or ''}"
        )
    return result


def git(cfg: Config, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return command(cfg, ["git", *args], check=check)


def load_state(cfg: Config) -> dict:
    if not cfg.state_file.exists():
        return {}
    try:
        return json.loads(cfg.state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(cfg: Config, state: dict) -> None:
    tmp = cfg.state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(cfg.state_file)


def plan_path(cfg: Config) -> Path:
    path = (cfg.repo / cfg.plan).resolve()
    try:
        path.relative_to(cfg.repo)
    except ValueError as exc:
        raise LoopError("--plan must point inside the repository") from exc
    return path


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean(cfg: Config) -> bool:
    return not git(cfg, "status", "--porcelain").stdout.strip()


def rollback(cfg: Config) -> None:
    log("discarding worker changes")
    git(cfg, "reset", "--hard", "HEAD", check=False)
    git(cfg, "clean", "-fd", check=False)


def dotenv_values(path: Path) -> dict[str, str]:
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


def guard_dotenv(cfg: Config) -> None:
    if cfg.allow_project_secrets:
        return
    values = dotenv_values(cfg.repo / ".env")
    exposed = sorted(key for key in SENSITIVE_DOTENV_KEYS if values.get(key))
    if exposed:
        raise LoopError(
            ".env contains service credentials visible to the autonomous worker: "
            + ", ".join(exposed)
            + ". Use a dedicated limited/budgeted gateway credential, remove the secret, "
            "or explicitly pass --allow-project-secrets."
        )


def sanitized_service_env() -> dict[str, str]:
    """Environment for outer project commands; do not leak parent service creds."""
    env = os.environ.copy()
    for key in SERVICE_ENV_KEYS:
        env.pop(key, None)
    return env


def write_schema(cfg: Config) -> None:
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


def prompt(cfg: Config) -> str:
    return f"""You are an autonomous software-engineering worker in a dedicated clone.

Read `{cfg.plan}` completely and execute that plan.

Rules:
- Inspect existing code before editing and keep changes scoped to the plan.
- Implement the whole plan and run relevant tests/checks.
- Fix failures caused by your changes.
- Do NOT run git pull/fetch/push/commit/reset/clean/checkout/switch/rebase or
  otherwise modify Git refs. The outer harness owns Git synchronization.
- Do NOT modify `{cfg.plan}` or `scripts/codex_loop.py`.
- Do NOT bypass sandbox, network, credential, proxy, rate-limit or budget guards.
- Do not ask questions. Make conservative implementation decisions yourself.
- Return `complete` only if the plan is finished; otherwise return `blocked`
  and explain the blocker.
"""


def codex_args(cfg: Config) -> list[str]:
    # shell_environment_policy affects shell commands spawned by Codex while the
    # Codex CLI can still use its own authenticated session/provider settings.
    excluded = json.dumps(sorted(SERVICE_ENV_KEYS), separators=(",", ":"))
    args = [
        "codex",
        "exec",
        "--sandbox",
        "workspace-write",
        "--ephemeral",
        "--color",
        "never",
        "--config",
        'approval_policy="never"',
        "--config",
        f"shell_environment_policy.exclude={excluded}",
    ]
    if cfg.allow_network:
        args += ["--config", "sandbox_workspace_write.network_access=true"]
    args += [
        "--output-schema",
        str(cfg.schema_file),
        "--output-last-message",
        str(cfg.result_file),
        "-",
    ]
    return args


def run_codex(cfg: Config) -> dict:
    guard_dotenv(cfg)
    cfg.result_file.unlink(missing_ok=True)

    dotenv = cfg.repo / ".env"
    dotenv_before = dotenv.read_bytes() if dotenv.exists() else None
    log("starting Codex")
    try:
        with cfg.log_file.open("w", encoding="utf-8") as out:
            result = subprocess.run(
                codex_args(cfg),
                cwd=cfg.repo,
                text=True,
                input=prompt(cfg),
                stdout=out,
                stderr=subprocess.STDOUT,
                timeout=cfg.timeout,
            )
    finally:
        # .env is ignored by Git, so preserve it explicitly even on crashes.
        if dotenv_before is None:
            dotenv.unlink(missing_ok=True)
        else:
            dotenv.write_bytes(dotenv_before)

    if result.returncode != 0:
        raise LoopError(f"Codex exited {result.returncode}; see {cfg.log_file}")
    if not cfg.result_file.exists():
        raise LoopError("Codex did not produce result.json")
    try:
        return json.loads(cfg.result_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise LoopError(f"invalid Codex result JSON: {exc}") from exc


def restore_protected(cfg: Config) -> None:
    for path in (cfg.plan, "scripts/codex_loop.py"):
        tracked = git(cfg, "ls-files", "--error-unmatch", path, check=False)
        if tracked.returncode == 0:
            git(cfg, "restore", "--source=HEAD", "--", path)


def count_text_lines_capped(path: Path, cap: int) -> int:
    total = 0
    saw_data = False
    try:
        with path.open("rb") as handle:
            while total <= cap:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                saw_data = True
                if b"\0" in chunk:
                    return 1  # binary file: count the file, not its byte size
                total += chunk.count(b"\n")
    except OSError:
        return cap + 1
    return total + (1 if saw_data else 0)


def diff_stats(cfg: Config) -> tuple[int, int]:
    """Count tracked + untracked changes; plain `git diff` misses new files."""
    files = 0
    lines = 0

    tracked = git(cfg, "diff", "--numstat", "HEAD", "--").stdout
    for row in tracked.splitlines():
        parts = row.split("\t", 2)
        if len(parts) != 3:
            continue
        files += 1
        added, removed = parts[0], parts[1]
        lines += int(added) if added.isdigit() else 1
        lines += int(removed) if removed.isdigit() else 1

    untracked = git(cfg, "ls-files", "--others", "--exclude-standard").stdout
    for relative in filter(None, untracked.splitlines()):
        files += 1
        lines += count_text_lines_capped(cfg.repo / relative, cfg.max_diff_lines)
        if lines > cfg.max_diff_lines:
            break

    return files, lines


def enforce_diff_budget(cfg: Config) -> None:
    files, lines = diff_stats(cfg)
    log(f"diff budget: {files}/{cfg.max_changed_files} files, {lines}/{cfg.max_diff_lines} lines")
    if files > cfg.max_changed_files:
        raise LoopError(f"changed-file budget exceeded: {files} > {cfg.max_changed_files}")
    if lines > cfg.max_diff_lines:
        raise LoopError(f"diff-line budget exceeded: {lines} > {cfg.max_diff_lines}")


def verify(cfg: Config) -> None:
    if not cfg.verify.strip():
        log("outer verification disabled")
        return
    args = shlex.split(cfg.verify, posix=os.name != "nt")
    result = command(
        cfg,
        args,
        check=False,
        timeout=cfg.timeout,
        env=sanitized_service_env(),
    )
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.returncode != 0:
        raise LoopError(f"verification failed with exit code {result.returncode}")


def register_failure(cfg: Config, state: dict, plan_hash: str, error: str) -> None:
    count = int(state.get("failure_count", 0)) + 1 if state.get("failing_plan_hash") == plan_hash else 1
    state.update(
        failing_plan_hash=plan_hash,
        failure_count=count,
        last_error=error,
        last_failure_at=datetime.now().isoformat(timespec="seconds"),
    )
    if count >= cfg.max_failures:
        state["blocked_plan_hash"] = plan_hash
        log(f"plan failed {count} times; marking this plan hash BLOCKED")
    save_state(cfg, state)


def iteration(cfg: Config) -> None:
    state = load_state(cfg)
    if not clean(cfg):
        log("working tree is dirty; skipping to protect manual work")
        return

    git(cfg, "checkout", cfg.branch)
    git(cfg, "pull", "--ff-only", "origin", cfg.branch)

    path = plan_path(cfg)
    if not path.exists():
        log(f"plan {cfg.plan!r} does not exist; nothing to do")
        return

    plan_hash = file_hash(path)
    if state.get("completed_plan_hash") == plan_hash:
        log(f"plan {plan_hash[:8]} already completed; waiting for a plan change")
        return
    if state.get("blocked_plan_hash") == plan_hash:
        log(f"plan {plan_hash[:8]} is BLOCKED; waiting for a plan change")
        return

    base_commit = git(cfg, "rev-parse", "HEAD").stdout.strip()
    try:
        result = run_codex(cfg)
        restore_protected(cfg)

        if result.get("status") != "complete":
            blocker = result.get("blocker") or "Codex reported blocked"
            log(f"Codex BLOCKED: {blocker}")
            rollback(cfg)
            state.update(blocked_plan_hash=plan_hash, failure_count=0, last_result=result)
            save_state(cfg, state)
            return

        if clean(cfg):
            log("plan is already satisfied; no repository changes required")
            state.update(
                completed_plan_hash=plan_hash,
                blocked_plan_hash=None,
                failure_count=0,
                last_result=result,
            )
            save_state(cfg, state)
            return

        enforce_diff_budget(cfg)
        verify(cfg)

        # Never autonomously rebase a stale agent patch. If main moved while the
        # model was working, throw the attempt away and retry from the new HEAD.
        git(cfg, "fetch", "origin", cfg.branch)
        remote_commit = git(cfg, "rev-parse", f"origin/{cfg.branch}").stdout.strip()
        if remote_commit != base_commit:
            log("remote changed during the Codex run; discarding stale attempt")
            git(cfg, "reset", "--hard", f"origin/{cfg.branch}")
            git(cfg, "clean", "-fd", check=False)
            return

        git(cfg, "add", "-A")
        git(cfg, "commit", "-m", f"codex-loop: execute {cfg.plan} {plan_hash[:8]}")
        git(cfg, "push", "origin", cfg.branch)
        commit = git(cfg, "rev-parse", "HEAD").stdout.strip()
        state.update(
            completed_plan_hash=plan_hash,
            blocked_plan_hash=None,
            failing_plan_hash=None,
            failure_count=0,
            last_result=result,
            completed_commit=commit,
            completed_at=datetime.now().isoformat(timespec="seconds"),
        )
        save_state(cfg, state)
        log(f"plan completed and pushed: {commit[:12]} ✅")

    except subprocess.TimeoutExpired:
        log("Codex/verification timed out")
        rollback(cfg)
        register_failure(cfg, state, plan_hash, "timeout")
    except Exception as exc:
        log(f"iteration failed: {exc}")
        rollback(cfg)
        register_failure(cfg, state, plan_hash, str(exc))


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def acquire_lock(cfg: Config) -> None:
    if cfg.lock_file.exists():
        try:
            old_pid = int(cfg.lock_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            old_pid = -1
        if pid_alive(old_pid):
            raise LoopError(f"another worker is running (pid={old_pid})")
        cfg.lock_file.unlink(missing_ok=True)
    cfg.lock_file.write_text(str(os.getpid()), encoding="utf-8")


def parse_args() -> Config:
    parser = argparse.ArgumentParser(description="Execute a GitHub Markdown plan with Codex in a loop.")
    parser.add_argument("--repo", default=".", help="dedicated worker clone (default: current directory)")
    parser.add_argument("--plan", required=True, help="Markdown plan path relative to repository root")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--interval", type=int, default=300)
    parser.add_argument("--timeout", type=int, default=3600)
    parser.add_argument("--max-failures", type=int, default=3)
    parser.add_argument("--max-changed-files", type=int, default=50)
    parser.add_argument("--max-diff-lines", type=int, default=5000)
    parser.add_argument("--verify", default=DEFAULT_VERIFY, help="outer verification command; empty disables")
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="enable Codex sandbox network; use only behind an OS/container allowlist or budget gateway",
    )
    parser.add_argument(
        "--allow-project-secrets",
        action="store_true",
        help="allow non-empty service API keys in .env (unsafe with unrestricted credentials)",
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

    cfg.runtime.mkdir(parents=True, exist_ok=True)
    write_schema(cfg)
    try:
        acquire_lock(cfg)
    except LoopError as exc:
        print(exc, file=sys.stderr)
        return 2

    log(f"repo={cfg.repo}")
    log(f"branch={cfg.branch} plan={cfg.plan} interval={cfg.interval}s")
    log(f"Codex network={'ENABLED' if cfg.allow_network else 'disabled'}")
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
        cfg.lock_file.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
