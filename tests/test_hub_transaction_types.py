"""Pyright distinguishes transaction capabilities at a production decision port."""

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_locked_decision_rejects_unlocked_transaction(tmp_path: Path) -> None:
    fixture = tmp_path / "transaction_diagnostic.py"
    fixture.write_text(
        "from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction\n"
        "from blizzard.hub.domain.chunk.ports.dependencies import IWriteChunkDependenciesRepository\n"
        "from datetime import datetime\n"
        "def calls(repo: IWriteChunkDependenciesRepository, unlocked: Transaction, locked: LockedTransaction, at: datetime) -> None:\n"
        "    repo.declare_in_transaction_locked(locked, 'a', 'b', by='test', at=at)\n"
        "    repo.declare_in_transaction_locked(unlocked, 'a', 'b', by='test', at=at)\n"
    )
    result = subprocess.run(
        ["pyright", "--outputjson", str(fixture)], capture_output=True, text=True, timeout=120, check=False
    )
    report = json.loads(result.stdout)
    errors = [item for item in report["generalDiagnostics"] if item["severity"] == "error"]
    assert result.returncode == 1, result.stdout + result.stderr
    assert len(errors) == 1, errors
    assert errors[0]["rule"] == "reportArgumentType"
    assert errors[0]["range"]["start"]["line"] == 5
