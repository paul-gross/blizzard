"""Short socket fixtures work with long scratch roots and preserve pytest failure artifacts."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    ("policy", "keep_pass", "keep_fail"), [("all", True, True), ("failed", False, True), ("none", False, False)]
)
def test_short_fixtures_preserve_failed_test_artifacts(
    tmp_path: Path, policy: str, keep_pass: bool, keep_fail: bool
) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_inner.py").write_text(
        """
import socket
import pytest

pytestmark = pytest.mark.component

@pytest.fixture
def tmp_path(short_tmp_path, tmp_path):
    return tmp_path

def test_pass(tmp_path, short_host_runtime):
    for root in (tmp_path, short_host_runtime):
        with socket.socket(socket.AF_UNIX) as sock:
            sock.bind(str(root / "host.sock"))
    (tmp_path / "passed.txt").write_text("passed")

def test_fail(tmp_path):
    (tmp_path / "failure.txt").write_text("retained")
    assert False
"""
    )
    scratch = tmp_path / ("long-root-" * 12)
    scratch.mkdir()
    base = scratch / "pytest"
    env = {**os.environ, "TMPDIR": str(scratch), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests.support.suite_plugin",
            "-o",
            f"tmp_path_retention_policy={policy}",
            f"--basetemp={base}",
            str(suite),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 failed, 1 passed" in result.stdout
    assert [p.read_text() for p in base.rglob("failure.txt")] == (["retained"] if keep_fail else [])
    assert [p.read_text() for p in base.rglob("passed.txt")] == (["passed"] if keep_pass else [])
