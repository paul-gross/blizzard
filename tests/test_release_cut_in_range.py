from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_SCRIPT = repo_root() / "scripts" / "release-cut-in-range.sh"
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.test",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.test",
}


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env={**os.environ, **_GIT_ENV}
    )
    return result.stdout.strip()


def _commit(repo: Path, message: str) -> None:
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)


@pytest.fixture
def pull_request(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "master")
    _commit(tmp_path, "root")
    _git(tmp_path, "tag", "base")
    return tmp_path


def _is_cut(repo: Path) -> str:
    result = subprocess.run(
        [str(_SCRIPT), "base", "HEAD"], cwd=repo, capture_output=True, text=True, env=os.environ, check=True
    )
    return result.stdout.strip()


def test_a_range_with_a_cut_commit_is_a_release_cut(pull_request: Path) -> None:
    _commit(pull_request, "change")
    _commit(pull_request, "chore: cut\n\nRelease-Cut: 0.2.1")
    assert _is_cut(pull_request) == "true"


def test_a_range_of_plain_commits_is_not(pull_request: Path) -> None:
    _commit(pull_request, "change")
    assert _is_cut(pull_request) == "false"


def test_an_empty_range_is_not(pull_request: Path) -> None:
    assert _is_cut(pull_request) == "false"


def test_a_cut_trailer_before_the_base_is_not_in_the_range(pull_request: Path) -> None:
    _commit(pull_request, "chore: cut\n\nRelease-Cut: 0.2.0")
    _git(pull_request, "tag", "-f", "base")
    _commit(pull_request, "change")
    assert _is_cut(pull_request) == "false"


def test_the_trailer_word_in_prose_is_not_a_cut(pull_request: Path) -> None:
    _commit(pull_request, "change\n\nThis is a Release-Cut: of sorts.\n\nMore prose follows here.")
    assert _is_cut(pull_request) == "false"
