from __future__ import annotations

from types import SimpleNamespace

from harness_rag import resume_dispatch, runtime


def test_find_candidate_commit_allows_harness_only_side_commits(monkeypatch) -> None:
    def fake_git(*args, **kwargs):
        if args[0] == "log":
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "ccc\tDocument stage resume\n"
                    "bbb\tharness: cycle 03 cycle-03-demo\n"
                    "aaa\tMake pushes best effort\n"
                ),
            )
        raise AssertionError(args)

    monkeypatch.setattr(resume_dispatch.orchestrator, "git", fake_git)
    monkeypatch.setattr(
        resume_dispatch,
        "_commit_paths",
        lambda sha: ["harness_rag/runtime.py"] if sha != "bbb" else ["src/product.py"],
    )

    assert (
        resume_dispatch._find_candidate_commit(
            "saved", "head", "harness: cycle 03 cycle-03-demo"
        )
        == "bbb"
    )


def test_git_push_failure_is_best_effort(monkeypatch) -> None:
    calls = []

    def fake_run(args, *, check=True, env=None):
        calls.append((args, check))
        return SimpleNamespace(returncode=128, stdout="403")

    monkeypatch.setattr(runtime, "run", fake_run)
    result = runtime.git("push", "origin", "branch")

    assert result.returncode == 128
    assert calls == [(["git", "push", "origin", "branch"], False)]
