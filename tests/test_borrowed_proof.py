"""``scripts/borrowed-proof.sh`` — whether a stable tag borrows its candidate's green run.
Drives the real script with a stub ``gh`` on ``PATH`` that prints the head branches of the
successful ``release`` runs at the tagged commit.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_SCRIPT = repo_root() / "scripts" / "borrowed-proof.sh"
_SHA = "a" * 40


def _run(tmp_path: Path, tag: str, run_branches: list[str]) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("#!/usr/bin/env bash\n" + "".join(f"echo {branch}\n" for branch in run_branches))
    gh.chmod(0o755)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "GH_REPO": "o/r"}
    return subprocess.run([str(_SCRIPT), tag, _SHA], capture_output=True, text=True, env=env)


@pytest.mark.parametrize("tag", ["v0.2.0", "v0.2.3", "v1.0.0"])
def test_a_stable_tag_with_a_green_same_version_candidate_run_borrows(tmp_path: Path, tag: str) -> None:
    result = _run(tmp_path, tag, [tag, f"{tag}-rc.2"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "borrowed=true"


def test_a_promotion_without_a_candidate_run_is_refused(tmp_path: Path) -> None:
    result = _run(tmp_path, "v0.2.0", ["v0.2.0", "v0.3.0-rc.1"])
    assert result.returncode != 0
    assert "v0.2.0-rc.K" in result.stderr
    assert result.stdout == ""


def test_a_major_promotion_without_a_candidate_run_is_refused(tmp_path: Path) -> None:
    assert _run(tmp_path, "v1.0.0", []).returncode != 0


def test_a_patch_without_a_candidate_run_runs_the_full_suite(tmp_path: Path) -> None:
    result = _run(tmp_path, "v0.2.1", ["v0.2.1"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "borrowed=false"


def test_a_different_versions_candidate_does_not_count(tmp_path: Path) -> None:
    assert _run(tmp_path, "v0.2.1", ["v0.2.10-rc.1", "v0.2.1x-rc.1"]).stdout.strip() == "borrowed=false"


def test_a_candidate_tag_never_borrows(tmp_path: Path) -> None:
    result = _run(tmp_path, "v0.2.0-rc.3", ["v0.2.0-rc.2"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "borrowed=false"
