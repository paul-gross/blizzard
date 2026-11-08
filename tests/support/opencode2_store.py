"""An OpenCode 2 store seeded with the contract fixture's catalog row, for tests that price a model."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from blizzard.runner.harness.opencode_v2.price_catalog import CATALOG_KEY

FIXTURE = (
    Path(__file__).resolve().parents[2] / "src/blizzard/runner/harness/contracts/opencode/2.2.6/models_catalog_row.json"
)


def seed_store(path: Path, *, body: object | None = None, key: str = CATALOG_KEY, raw_value: str | None = None) -> Path:
    """An OpenCode 2 store at ``path`` holding the fixture's catalog row (or ``body`` in its place)."""
    row = json.loads(FIXTURE.read_text())
    if body is not None:
        row["body"] = json.dumps(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("create table kv (key text primary key, value text, time_created int, time_updated int)")
    connection.execute("insert into kv values (?, ?, 0, 0)", (key, json.dumps(row) if raw_value is None else raw_value))
    connection.commit()
    connection.close()
    return path
