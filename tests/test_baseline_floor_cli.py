"""Operator entry points present the baseline refusal without modifying an existing store."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.cli.main import blizzard
from blizzard.hub import runtime as hub_runtime
from blizzard.runner import runtime as runner_runtime
from tests.config_inputs import init_host, init_hub
from tests.test_baseline_floor import make_tree

pytestmark = pytest.mark.component


@pytest.mark.parametrize("daemon", ["hub", "runner"])
def test_operator_entry_points_refuse_absorbed_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, daemon: str
) -> None:
    root = tmp_path / daemon
    runtime = hub_runtime if daemon == "hub" else runner_runtime
    (init_hub if daemon == "hub" else init_host)(root)
    db = root / "data" / ("hub.db" if daemon == "hub" else "host.db")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE alembic_version SET version_num = 'old_rev'")
        conn.commit()
    monkeypatch.setattr(runtime, "MIGRATIONS_DIR", make_tree(tmp_path))

    verbs = [["migrate", "--dir", str(root)], ["migrate", "--dir", str(root), "--down", "base"]]
    verbs.append(["init", str(root)])
    if daemon == "runner":
        verbs.extend((["host", "--check", "--dir", str(root)], ["host", "--dir", str(root)]))
    else:
        verbs.append(["host", "--dir", str(root)])
    for verb in verbs:
        result = CliRunner().invoke(blizzard, [daemon, *verb])
        assert result.exit_code != 0, (verb, result.output)
        assert "old_rev" in result.output and "v0.2.0" in result.output, (verb, result.output)
        assert "Traceback" not in result.output
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == ("old_rev",)
