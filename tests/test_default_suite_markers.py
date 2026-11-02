"""Every collected test carries one of the declared verification-tier markers.

An unmarked test still runs in the default suite (nothing in collection skips it) but is
invisible to both ``-m unit`` and ``-m component``, so their totals silently under-count
the default run instead of the gap failing a run outright.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.suite_plugin import UNMARKED_TEST_NODEIDS, pytest_ignore_collect

pytestmark = pytest.mark.unit


def test_every_collected_test_carries_a_tier_marker() -> None:
    assert UNMARKED_TEST_NODEIDS == [], (
        f"these collected tests carry none of unit/component/service/e2e/crash_sweep/rehearsal/store_baseline/winter_binding/postgres_lock: {UNMARKED_TEST_NODEIDS}"
    )


def test_postgres_lock_collection_is_explicit(monkeypatch: pytest.MonkeyPatch, pytestconfig: pytest.Config) -> None:
    suite = Path("tests/postgres_locks")
    monkeypatch.delenv("BLIZZARD_POSTGRES_LOCK_TEST", raising=False)
    assert pytest_ignore_collect(suite, pytestconfig) is True
    assert pytest_ignore_collect(Path("tests/other"), pytestconfig) is None
    monkeypatch.setenv("BLIZZARD_POSTGRES_LOCK_TEST", "1")
    assert pytest_ignore_collect(suite, pytestconfig) is None


def test_postgres_lock_explicit_file_still_requires_opt_in() -> None:
    env = os.environ.copy()
    env.pop("BLIZZARD_POSTGRES_LOCK_TEST", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "tests/postgres_locks/test_admission.py"],
        cwd=Path(__file__).resolve().parent.parent,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "postgres lock tests require BLIZZARD_POSTGRES_LOCK_TEST=1" in result.stderr
