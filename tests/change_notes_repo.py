"""Helpers for the change notes render tests, shared with other test modules."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.repo_files import repo_root


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, message: str, files: dict[str, str]) -> None:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git(repo, "add", ".")
    git(repo, "commit", "-m", message)


def _fixed(text: str) -> str:
    return f"---\nkind: fixed\n---\n\n{text}\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    (path / "scripts").mkdir(parents=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    shutil.copy(repo_root() / "scripts" / "release-notes.sh", path / "scripts" / "release-notes.sh")
    commit(path, "feat: first", {"changes/0.1.0/one.md": _fixed("Fixes one."), "README.md": "x"})
    git(path, "tag", "v0.1.0")
    commit(
        path,
        "fix: second",
        {"changes/0.2.0/two.md": _fixed("Fixes two."), "changes/0.2.0/shared.md": _fixed("Shared.")},
    )
    git(path, "tag", "v0.2.0")
    git(path, "checkout", "-q", "-b", "release/0.1", "v0.1.0")
    commit(path, "fix: patch", {"changes/0.1.1/shared.md": _fixed("Shared.")})
    git(path, "tag", "v0.1.1")
    git(path, "checkout", "-q", "main")
    return path
