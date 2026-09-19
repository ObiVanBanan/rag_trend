from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys

import pytest

from harness_rag import runtime


def test_changed_paths_ignores_git_warning_lines(monkeypatch) -> None:
    outputs = iter(
        [
            "src/example.py\n",
            (
                "warning: could not open directory '.pytest_tmp/': Permission denied\n"
                "warning: could not open directory '.tmp_pytest/pytest-of-user/': Permission denied\n"
                "openspec/changes/demo/proposal.md\n"
            ),
        ]
    )

    def fake_git(*args: str, **kwargs):
        return SimpleNamespace(stdout=next(outputs), returncode=0)

    monkeypatch.setattr(runtime, "git", fake_git)

    assert runtime.changed_paths() == {
        "src/example.py",
        "openspec/changes/demo/proposal.md",
    }


def test_changed_paths_treats_only_warning_output_as_clean(monkeypatch) -> None:
    outputs = iter(
        [
            "",
            (
                "warning: could not open directory '.pytest_tmp/': Permission denied\n"
                "warning: could not open directory '.tmp_pytest/pytest-of-user/': Permission denied\n"
            ),
        ]
    )

    def fake_git(*args: str, **kwargs):
        return SimpleNamespace(stdout=next(outputs), returncode=0)

    monkeypatch.setattr(runtime, "git", fake_git)

    assert runtime.changed_paths() == set()


def test_clean_ignores_git_warning_lines(monkeypatch) -> None:
    def fake_git(*args: str, **kwargs):
        return SimpleNamespace(
            stdout="warning: could not open directory '.tmp_pytest/pytest-of-user/': Permission denied\n",
            returncode=0,
        )

    monkeypatch.setattr(runtime, "git", fake_git)

    assert runtime.clean() is True



def test_process_lock_rejects_second_harness_process(monkeypatch, tmp_path: Path) -> None:
    lock_path = tmp_path / "rag-harness.process.lock"
    state_dir = tmp_path / "state"
    monkeypatch.setattr(runtime, "_repo_process_lock_path", lambda: lock_path)

    code = f"""
from pathlib import Path
import time
from harness_rag import runtime

runtime._repo_process_lock_path = lambda: Path({str(lock_path)!r})
with runtime.harness_process_lock(Path({str(state_dir)!r})):
    print("LOCKED", flush=True)
    time.sleep(30)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "LOCKED"
        with pytest.raises(runtime.HarnessError, match="HARNESS_ALREADY_RUNNING"):
            with runtime.harness_process_lock(state_dir):
                pass
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)


def test_stale_owner_metadata_does_not_block_when_os_lock_is_free(
    monkeypatch, tmp_path: Path
) -> None:
    lock_path = tmp_path / "rag-harness.process.lock"
    owner_path = lock_path.with_suffix(lock_path.suffix + ".owner.json")
    owner_path.write_text('{"pid":999999,"state_dir":"stale"}', encoding="utf-8")
    monkeypatch.setattr(runtime, "_repo_process_lock_path", lambda: lock_path)

    with runtime.harness_process_lock(tmp_path / "state"):
        assert owner_path.exists()

    assert not owner_path.exists()
