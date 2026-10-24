"""``scripts/check-version-tag.sh`` — the pyproject.toml <-> release-tag version
agreement and line-reachability guard. Drives the real script against a scratch git
repository carrying its own ``pyproject.toml`` and ``origin/…`` refs (the script ``cd``s to
its own repo root, so a tmp_path copy isolates it from this repo's real version and history).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_REPO_ROOT = repo_root()
_SCRIPT = _REPO_ROOT / "scripts" / "check-version-tag.sh"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def _scratch_repo(tmp_path: Path, version: str, *, on_line: bool = True, window: str | None = None) -> Path:
    """A one-commit repository whose HEAD is the tagged commit; `origin/release/<X.Y>` points
    at it when `on_line`, and `origin/master` always does. `window` is window.toml's content."""
    root = tmp_path / "scratch"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(_SCRIPT, root / "scripts" / "check-version-tag.sh")
    (root / "pyproject.toml").write_text(f'[project]\nname = "scratch"\nversion = "{version}"\n')
    if window is not None:
        (root / "src" / "blizzard" / "wire").mkdir(parents=True)
        (root / "src" / "blizzard" / "wire" / "window.toml").write_text(window)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "cut")
    head = _git(root, "rev-parse", "HEAD")
    _git(root, "update-ref", "refs/remotes/origin/master", head)
    if on_line:
        major_minor = ".".join(version.split(".")[:2])
        _git(root, "update-ref", f"refs/remotes/origin/release/{major_minor}", head)
    return root


def _run(root: Path, tag: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(root / "scripts" / "check-version-tag.sh"), tag],
        capture_output=True,
        text=True,
    )


def test_matching_version_and_tag_passes(tmp_path: Path) -> None:
    root = _scratch_repo(tmp_path, "1.2.3")
    result = _run(root, "v1.2.3")
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_mismatched_version_fails_naming_both(tmp_path: Path) -> None:
    root = _scratch_repo(tmp_path, "1.2.3")
    result = _run(root, "v9.9.9")
    assert result.returncode != 0
    assert "1.2.3" in result.stderr
    assert "9.9.9" in result.stderr


def test_non_v_prefixed_tag_is_rejected(tmp_path: Path) -> None:
    root = _scratch_repo(tmp_path, "1.2.3")
    result = _run(root, "1.2.3")
    assert result.returncode != 0


@pytest.mark.parametrize("tag", ["v0.2.0-rc.1", "v0.2.0-rc.2", "v0.2.0"])
def test_candidate_and_stable_tags_match_the_cut_version(tmp_path: Path, tag: str) -> None:
    root = _scratch_repo(tmp_path, "0.2.0")
    result = _run(root, tag)
    assert result.returncode == 0, result.stderr


def test_candidate_tag_rejects_literal_candidate_version(tmp_path: Path) -> None:
    root = _scratch_repo(tmp_path, "0.2.0-rc.1")
    result = _run(root, "v0.2.0-rc.1")
    assert result.returncode != 0
    assert "0.2.0-rc.1" in result.stderr
    assert "pyproject.toml's [project] version is '0.2.0-rc.1'" in result.stderr


def test_a_tag_off_its_line_fails_naming_the_release_branch(tmp_path: Path) -> None:
    root = _scratch_repo(tmp_path, "0.2.0", on_line=False)
    result = _run(root, "v0.2.0")
    assert result.returncode != 0
    assert "origin/release/0.2" in result.stderr


def test_a_tag_on_its_line_passes(tmp_path: Path) -> None:
    root = _scratch_repo(tmp_path, "0.2.1")
    assert _run(root, "v0.2.1").returncode == 0


def test_a_tag_on_an_older_commit_of_its_line_passes(tmp_path: Path) -> None:
    root = _scratch_repo(tmp_path, "0.2.0")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "later")
    _git(root, "update-ref", "refs/remotes/origin/release/0.2", "HEAD")
    _git(root, "checkout", "-q", "HEAD~1")
    assert _run(root, "v0.2.0").returncode == 0


@pytest.mark.parametrize(
    ("window", "tag", "passes"),
    [
        ('floor = "0.2"\nopening = "1"\n', "v1.0.0-rc.1", True),
        ('floor = "0.2"\n', "v1.0.0-rc.1", False),
        ('floor = "0.2"\nopening = "2"\n', "v1.0.0-rc.1", False),
        ('floor = "0.2"\nopening = "1"\n', "v1.0.0", False),
        ('floor = "0.2"\nopening = "1"\n', "v1.1.0-rc.1", False),
    ],
)
def test_an_open_masters_major_candidate_passes_only_while_the_window_opens_that_major(
    tmp_path: Path, window: str, tag: str, passes: bool
) -> None:
    version = tag[1:].split("-rc.")[0]
    root = _scratch_repo(tmp_path, version, on_line=False, window=window)
    result = _run(root, tag)
    assert (result.returncode == 0) is passes, result.stderr
