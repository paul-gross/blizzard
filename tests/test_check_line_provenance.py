from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_SCRIPT = repo_root() / "scripts" / "check-line-provenance.sh"
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


def _commit(repo: Path, message: str) -> str:
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def line(tmp_path: Path) -> dict[str, Any]:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "master")
    _commit(seed, "root")
    _git(seed, "checkout", "-q", "-b", "side")
    off_master = _commit(seed, "never merged")
    _git(seed, "checkout", "-q", "master")
    on_master = _commit(seed, "fix on master")
    _git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    repo = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(repo))
    _git(repo, "fetch", "-q", "origin", "side")
    _git(repo, "checkout", "-q", "-b", "release/0.2", "master~1")
    return {"repo": repo, "base": _git(repo, "rev-parse", "HEAD"), "on_master": on_master, "off_master": off_master}


def _check(line: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_SCRIPT), line["base"], "HEAD"], cwd=line["repo"], capture_output=True, text=True, env=os.environ
    )


def test_a_cherry_pick_of_a_master_commit_passes(line) -> None:
    _commit(line["repo"], f"fix\n\n(cherry picked from commit {line['on_master']})")
    assert _check(line).returncode == 0


def test_a_line_only_commit_passes(line) -> None:
    _commit(line["repo"], "pin\n\nLine-only: master has no such file")
    assert _check(line).returncode == 0


def test_a_cut_commit_passes(line) -> None:
    _commit(line["repo"], "chore: cut\n\nRelease-Cut: 0.2.1")
    assert _check(line).returncode == 0


def test_a_plain_commit_fails_and_is_named(line) -> None:
    sha = _commit(line["repo"], "just a change")
    result = _check(line)
    assert result.returncode == 1
    assert sha[:7] in result.stderr


def test_a_cherry_pick_of_a_commit_not_on_master_fails_and_is_named(line) -> None:
    sha = _commit(line["repo"], f"fix\n\n(cherry picked from commit {line['off_master']})")
    result = _check(line)
    assert result.returncode == 1
    assert sha[:7] in result.stderr


def test_a_second_cherry_pick_line_on_master_qualifies(line) -> None:
    _commit(
        line["repo"],
        f"fix\n\n(cherry picked from commit {line['off_master']})\n(cherry picked from commit {line['on_master']})",
    )
    assert _check(line).returncode == 0


def test_a_trailer_word_in_prose_is_not_a_trailer(line) -> None:
    sha = _commit(line["repo"], "change\n\nThis commit is Line-only: honest.\n\nMore prose follows here.")
    result = _check(line)
    assert result.returncode == 1
    assert sha[:7] in result.stderr


def test_every_offending_commit_is_named(line) -> None:
    first = _commit(line["repo"], "one")
    _commit(line["repo"], "ok\n\nLine-only: reason")
    second = _commit(line["repo"], "two")
    result = _check(line)
    assert first[:7] in result.stderr and second[:7] in result.stderr
