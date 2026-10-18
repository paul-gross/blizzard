"""``scripts/check-version-tag.sh`` — the pyproject.toml <-> release-tag version
agreement guard. Drives the real script against a scratch repo
root carrying its own ``pyproject.toml`` (the script ``cd``s to its own repo
root, so a tmp_path copy with a synthetic ``pyproject.toml`` isolates it from
this repo's real version).
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


def _scratch_repo(tmp_path: Path, version: str) -> Path:
    root = tmp_path / "scratch"
    (root / "scripts").mkdir(parents=True)
    shutil.copy(_SCRIPT, root / "scripts" / "check-version-tag.sh")
    (root / "pyproject.toml").write_text(f'[project]\nname = "scratch"\nversion = "{version}"\n')
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
