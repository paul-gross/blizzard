"""Pyright distinguishes transaction capabilities; no production decision uses this signature yet."""

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_locked_decision_rejects_unlocked_transaction(tmp_path: Path) -> None:
    fixture = tmp_path / "transaction_diagnostic.py"
    fixture.write_text(
        "from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction\n"
        "def decision(token: LockedTransaction) -> None: ...\n"
        "def calls(unlocked: Transaction, locked: LockedTransaction) -> None:\n"
        "    decision(locked)\n"
        "    decision(unlocked)\n"
    )
    result = subprocess.run(
        ["pyright", "--outputjson", str(fixture)], capture_output=True, text=True, timeout=120, check=False
    )
    report = json.loads(result.stdout)
    errors = [item for item in report["generalDiagnostics"] if item["severity"] == "error"]
    assert result.returncode == 1, result.stdout + result.stderr
    assert len(errors) == 1, errors
    assert errors[0]["rule"] == "reportArgumentType"
    assert errors[0]["range"]["start"]["line"] == 4
