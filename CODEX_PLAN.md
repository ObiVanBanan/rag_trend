# CODEX PLAN — Harden autonomous Codex loop V2

## Goal

Create a hardened replacement for the autonomous Codex worker without modifying the currently running/protected `scripts/codex_loop.py`.

The current worker protects `scripts/codex_loop.py`, so this plan MUST be executable by the current loop. Implement the replacement as:

- `scripts/codex_loop_v2.py`
- tests in `tests/test_codex_loop_v2.py`

Do **not** modify production retrieval, Eval V2 retrieval logic, reranker logic, human review data, or golden labels in this wave.

After this wave is reviewed and approved, the user will switch the worker process from `scripts/codex_loop.py` to `scripts/codex_loop_v2.py` once. Future plans will continue to use `CODEX_PLAN.md`.

---

## Why this wave exists

The current loop has several correctness/safety problems that are unacceptable for an unattended worker.

### P1 — push failure can strand an unpushed local commit

Current failure path can be:

```text
Codex edits files
→ verify passes
→ git commit succeeds
→ git push fails
→ exception
→ rollback = git reset --hard HEAD
```

After `git commit`, `HEAD` already points to the new local Codex commit, so `reset --hard HEAD` does not roll anything back.

On the next iteration the local branch can remain ahead of origin. The plan may look already satisfied locally and be marked completed without the remote ever receiving the commit.

### P1 — `os.kill(pid, 0)` is unsafe on Windows

Do not use `os.kill(pid, 0)` as a process-liveness probe on Windows.

The replacement lock must be a real OS-level exclusive lock that is released automatically when the worker exits/crashes. Do not emulate liveness by sending signal 0 on Windows.

### P1/P2 — Git invariants are prompt-only today

The prompt tells Codex not to run Git operations, but the harness must not trust that instruction as an invariant.

The outer worker must detect if Codex changed:

- current branch;
- `HEAD` commit;
- protected plan/runner files.

A Codex-created local commit must never be treated as a clean successful run.

### P2 — diff budget is checked only before outer verification

Outer verification can itself create or modify files after the first budget check. The worker currently stages everything afterwards.

The final change set must be checked again after verification and before commit.

---

# Required implementation

## 1. Create `scripts/codex_loop_v2.py`

Start from the current `scripts/codex_loop.py` behavior, but implement the fixes below.

Do not import the old runner and do not modify it.

Keep the same normal CLI shape where reasonable:

```bash
python scripts/codex_loop_v2.py --plan CODEX_PLAN.md
```

Default branch remains `main`.
Default polling interval remains 300 seconds.
Default outer verification remains:

```bash
python -m pytest -q
```

State/log files may remain under `.git/codex-loop/`, but avoid corrupting an active V1 worker state. Prefer a separate runtime directory such as:

```text
.git/codex-loop-v2/
```

---

## 2. Implement a real cross-platform process lock

Do not use `os.kill(pid, 0)` on Windows.

Use an OS-level exclusive file lock held for the lifetime of the worker process.

Recommended stdlib approach:

- POSIX: `fcntl.flock(..., LOCK_EX | LOCK_NB)`
- Windows: `msvcrt.locking(..., LK_NBLCK, 1)` or another safe stdlib/ctypes file-lock implementation that does not signal/terminate the target process.

Requirements:

- first worker acquires lock;
- second worker immediately fails with a clear `LoopError` / exit code 2;
- lock is automatically released by the OS when the process exits/crashes;
- stale PID files must not permanently block the worker;
- no `os.kill(pid, 0)` on Windows;
- do not solve this by simply deleting an existing lock file before taking the lock.

Keep the open file descriptor/handle alive until shutdown.

---

## 3. Make rollback baseline-aware

At the start of each execution attempt, after checkout/pull/fetch has completed, record:

```python
base_commit = git rev-parse HEAD
base_branch = current branch
```

Replace generic `rollback()` behavior with something equivalent to:

```text
rollback_to(base_commit)
    git reset --hard <base_commit>
    git clean -fd
```

Every failure after the attempt starts must restore the repository to the recorded `base_commit`, not to the current `HEAD`.

This includes failures from:

- Codex timeout;
- invalid result JSON;
- Codex reports blocked after making changes;
- diff budget;
- verification;
- commit;
- push;
- remote verification.

### Push-failure acceptance case

Simulate:

```text
base A
Codex change
commit B
push fails
```

After failure:

```text
HEAD == A
working tree clean
plan is NOT marked complete
```

The next loop iteration must be able to retry from origin cleanly.

---

## 4. Enforce branch and HEAD invariants after Codex

The prompt is not sufficient protection.

Immediately after `run_codex()` and before accepting `clean()` as success, verify:

```text
current branch == configured branch
HEAD == base_commit
```

If Codex changed branch or created/reset/amended a commit:

- treat the attempt as failed;
- rollback to `base_commit`;
- do not mark plan completed;
- do not push anything.

Also perform the invariant check again before creating the outer harness commit.

### Important regression case

If Codex illegally runs `git commit` and leaves a clean working tree, the worker MUST NOT execute this logic:

```text
clean tree → plan already satisfied → mark completed
```

It must detect `HEAD != base_commit` and fail/rollback instead.

---

## 5. Protect the plan and the V2 runner dynamically

The new worker must protect:

- configured plan path, normally `CODEX_PLAN.md`;
- its own script path (`scripts/codex_loop_v2.py`).

Do not hardcode protection only for `scripts/codex_loop.py`.

Derive the runner path from `__file__` relative to the repository when possible.

After Codex returns:

- restore protected tracked files from `base_commit`/HEAD baseline;
- verify they are unchanged.

Do this again after outer verification before commit, because verification can also modify files.

---

## 6. Re-check diff budget after verification

Required sequence:

```text
Codex finished
→ restore protected files
→ Git invariant check
→ diff budget check #1
→ outer verification
→ restore protected files again
→ Git invariant check again
→ diff budget check #2
→ commit
→ push
```

The second diff-budget check is mandatory.

Do not stage/commit files created by verification if they make the final diff exceed configured limits.

### Binary/untracked sanity

Current line counting treats a binary file as approximately one line. Add a conservative byte-size guard for untracked files so a huge binary artifact cannot bypass `max_diff_lines` trivially.

A simple additional CLI/config limit such as `--max-untracked-bytes` is acceptable.

Keep defaults conservative but practical for this repository.

---

## 7. Make remote publication verifiable

Use explicit push semantics, for example:

```bash
git push origin HEAD:main
```

using the configured branch rather than assuming current implicit state.

After successful push:

1. fetch the configured branch;
2. read `origin/<branch>` SHA;
3. verify it equals the local committed SHA;
4. only then write `completed_plan_hash` to state.

If push or remote confirmation fails:

- rollback local repo to `base_commit`;
- register failure;
- do not mark the plan completed.

The state machine must never claim completion before remote publication is confirmed.

---

## 8. Handle unexpected local branch state conservatively

Before executing a plan, after fetching origin, compare local configured branch HEAD with `origin/<branch>`.

The worker is intended for a dedicated clone, but do not silently delete unexplained local commits.

If the local branch is ahead/diverged before a new attempt:

- stop that iteration with a clear error;
- do not mark plan blocked/completed;
- do not silently reset user/manual commits.

It is acceptable to require manual recovery for a pre-existing divergent dedicated clone.

Normal clean synchronized state should continue automatically.

---

## 9. Keep service-secret behavior conservative

Preserve the useful existing protections:

- do not leak service env variables into outer verification;
- preserve `.env` bytes around Codex execution;
- reject non-empty sensitive service keys in `.env` unless explicitly allowed;
- network remains disabled by default;
- Codex uses `workspace-write` and `approval_policy=never`.

Do not broaden permissions or network access in this wave.

---

# Tests

Create `tests/test_codex_loop_v2.py`.

Tests must not invoke real Codex, real GitHub, or network.
Use temporary repositories and/or monkeypatch/subprocess stubs.

At minimum cover the following.

## Test 1 — Windows lock does not use `os.kill(pid, 0)`

Exercise/mock the Windows lock path.

Acceptance:

- no call that can terminate another process;
- second lock acquisition fails cleanly while first is held.

## Test 2 — lock releases after close

Acquire lock, release/close it, then acquire it again successfully.

## Test 3 — push failure restores baseline

Scenario:

```text
base A
changes
outer commit B
push failure
```

Acceptance:

```text
HEAD == A
working tree clean
completed_plan_hash not set
```

## Test 4 — Codex-created commit is rejected

Simulate Codex changing `HEAD` while leaving the tree clean.

Acceptance:

- attempt fails;
- reset to base commit;
- plan is not marked complete.

## Test 5 — Codex branch switch is rejected

Simulate current branch changing during Codex execution.

Acceptance:

- attempt fails and rolls back/recover safely;
- no push.

## Test 6 — verification side-effect is included in final budget

Make the verification stub create an extra untracked file after budget check #1.

Acceptance:

- budget check #2 sees it;
- over-budget change is not committed.

## Test 7 — huge untracked binary is rejected by byte guard

A large binary file must not count as only one harmless line and pass all budgets.

## Test 8 — completion requires remote SHA confirmation

Simulate local commit SHA `B` and remote still at `A` after attempted push/confirmation.

Acceptance:

- plan is not marked complete.

Then simulate remote SHA `B`.

Acceptance:

- completion state may be written.

## Test 9 — plan outside repository is rejected

Keep the existing path traversal/symlink escape protection.

## Test 10 — protected V2 runner and plan cannot be committed as task output

Simulate changes to both files during Codex/verification.

Acceptance:

- they are restored before final commit;
- final diff does not include them.

---

# Validation

Run at least:

```bash
python -m pytest -q
```

If useful also run:

```bash
python scripts/codex_loop_v2.py --help
```

Do not start a real long-running loop as part of tests.
Do not invoke real Codex.
Do not push manually; the outer V1 harness owns publication for this plan.

---

# Files expected to change

Expected:

```text
scripts/codex_loop_v2.py
tests/test_codex_loop_v2.py
```

Optional only if genuinely needed:

```text
README.md
```

Do not modify:

```text
scripts/codex_loop.py
CODEX_PLAN.md
data/eval_v2_human_review.json
data/eval_v2_retrieval_miss_human_review.json
data/eval_labels_v2.json
src/nomenclature_matcher/*
```

Do not regenerate Eval artifacts in this wave.

---

# Definition of Done

This wave is complete only when all are true:

- `scripts/codex_loop_v2.py` exists;
- old `scripts/codex_loop.py` is unchanged;
- Windows lock does not use `os.kill(pid, 0)`;
- lock is exclusive and automatically releasable;
- failures after a local commit reset to the recorded base commit;
- Codex-created Git commits/branch changes are detected and rejected;
- diff budget runs before and after verification;
- huge untracked binaries have a byte-size guard;
- protected plan + V2 runner cannot leak into an autonomous task commit;
- successful push is confirmed by remote SHA before state is marked complete;
- pre-existing local ahead/diverged state is not silently destroyed;
- regression tests cover the listed cases;
- `python -m pytest -q` passes;
- no production retrieval or Eval semantics changed.

STOP after this hardening wave. Do not start retrieval tuning or full-catalog label finalization in the same commit.

---

# Final report

Return only:

1. changed files;
2. lock implementation summary;
3. rollback/push-failure behavior;
4. Git invariant enforcement summary;
5. post-verification diff-budget behavior;
6. pytest result;
7. confirmation old `scripts/codex_loop.py` was not modified;
8. any blocker if present.
