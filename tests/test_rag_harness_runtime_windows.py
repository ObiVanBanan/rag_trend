from __future__ import annotations

from types import SimpleNamespace

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
