"""What OpenCode 2 does to an OpenCode 1 store the first time it opens it, observed on a disposable store under an
isolated home, never the operator's."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from blizzard.runner.harness.compatibility import EvidenceState
from blizzard.runner.harness.json_value import JsonObject, JsonValue, decode_json
from blizzard.runner.harness.opencode_v2.compatibility.host import IProofHost
from blizzard.runner.harness.opencode_v2.compatibility.rows import ProofRow, RowObservation

#: The `kv` key OpenCode 2 writes when it has migrated a 1.x store; OpenCode 1's health reads it.
MIGRATION_KV_KEY = "migration.v1-v2"
SEED_PROMPT = "Reply with the word SEEDED and nothing else."
_STORE = Path(".local") / "share" / "opencode" / "opencode.db"


class IStoreMigrationProbe(Protocol):
    def observe(self) -> RowObservation: ...

    def captures(self) -> dict[str, JsonValue]:
        """The fixtures the observation left, keyed by name, for the OpenCode 1 corpus."""
        ...


def snapshot(path: Path) -> JsonObject:
    """Every table's schema and row count, and the ``kv`` rows when there are any."""
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as db:
        tables = [
            (str(name), str(sql or ""))
            for name, sql in db.execute("select name, sql from sqlite_master where type = 'table' order by name")
        ]
        counts: JsonObject = {
            name: int(db.execute(f'select count(*) from "{name}"').fetchone()[0]) for name, _ in tables
        }
        kv: JsonObject = {}
        if any(name == "kv" for name, _ in tables):
            for key, value in db.execute("select key, value from kv"):
                try:
                    kv[str(key)] = decode_json(value)
                except ValueError:
                    kv[str(key)] = str(value)
        rows: JsonObject = {}
        for name in ("session", "message", "part"):
            if name in counts:
                rows[name] = [[str(cell) for cell in row] for row in db.execute(f'select * from "{name}" order by 1')]
    return {"schema": dict(tables), "counts": counts, "kv": kv, "rows": rows}


def first_session_id(store: JsonObject) -> str:
    rows = store.get("rows")
    sessions = rows.get("session") if isinstance(rows, dict) else None
    first = sessions[0] if isinstance(sessions, list) and sessions else None
    return str(first[0]) if isinstance(first, list) and first else ""


@dataclass
class StoreMigrationProbe:
    """Seeds a 1.x store with OpenCode 1, lets OpenCode 2 open it, and has OpenCode 1 read it again."""

    binary: str
    opencode1: str | None
    opencode1_model: str | None
    root: Path
    host: IProofHost
    _captures: dict[str, JsonValue] = field(default_factory=dict[str, JsonValue])

    def captures(self) -> dict[str, JsonValue]:
        return dict(self._captures)

    def observe(self) -> RowObservation:
        if self.opencode1 is None or self.opencode1_model is None:
            return RowObservation(
                ProofRow.STORE_MIGRATION,
                EvidenceState.AMBIGUOUS,
                "not observed: no OpenCode 1 binary and model were given to seed a disposable 1.x store",
            )
        home, work = self.root / "home", self.root / "work"
        work.mkdir(parents=True, exist_ok=True)
        self.host.run(["git", "init", "-q", "."], cwd=work, timeout=30)
        v1_version = self.host.run([self.opencode1, "--version"], cwd=work, timeout=30, home=home).stdout.strip()
        seed = self.host.run(
            [self.opencode1, "run", "--model", self.opencode1_model, SEED_PROMPT], cwd=work, timeout=240, home=home
        )
        store = home / _STORE
        if seed.exit_code != 0 or not store.is_file():
            return RowObservation(
                ProofRow.STORE_MIGRATION, EvidenceState.FAILED, "OpenCode 1 did not seed a disposable store"
            )
        before = snapshot(store)
        opened = self.host.run(
            [self.binary, "api", "--standalone", "GET", "/api/session"], cwd=work, timeout=240, home=home
        )
        after = snapshot(store)
        listed = self.host.run([self.opencode1, "session", "list"], cwd=work, timeout=60, home=home)
        session_id = first_session_id(before)
        exported = self.host.run([self.opencode1, "export", session_id], cwd=work, timeout=60, home=home)
        before_schema = before["schema"] if isinstance(before["schema"], dict) else {}
        after_schema = after["schema"] if isinstance(after["schema"], dict) else {}
        before_rows = before["rows"] if isinstance(before["rows"], dict) else {}
        after_rows = after["rows"] if isinstance(after["rows"], dict) else {}
        kv = after["kv"] if isinstance(after["kv"], dict) else {}
        facts: JsonObject = {
            "opencode1_version": v1_version,
            "opened_exit_code": opened.exit_code,
            "same_file": store.is_file(),
            "signal": {MIGRATION_KV_KEY: kv.get(MIGRATION_KV_KEY)},
            "v1_tables_unchanged": all(
                before_schema.get(name) == after_schema.get(name) and before_rows.get(name) == after_rows.get(name)
                for name in ("session", "message", "part")
            ),
            "added_tables": list[JsonValue](sorted(set(after_schema) - set(before_schema))),
            "dropped_tables": list[JsonValue](sorted(set(before_schema) - set(after_schema))),
            "counts_before": before["counts"],
            "counts_after": after["counts"],
            "opencode1_lists_after": listed.exit_code == 0 and session_id in listed.stdout,
            "opencode1_exports_after": exported.exit_code == 0 and session_id in exported.stdout,
        }
        self._captures["store_migration"] = facts
        reads_back = bool(facts["opencode1_lists_after"]) and bool(facts["opencode1_exports_after"])
        ok = MIGRATION_KV_KEY in kv and opened.exit_code == 0 and reads_back
        return RowObservation(
            ProofRow.STORE_MIGRATION,
            EvidenceState.OBSERVED if ok else EvidenceState.FAILED,
            f"OpenCode 2 migrated the {v1_version} store in place and marked it with `kv` `{MIGRATION_KV_KEY}`; "
            "OpenCode 1 still lists and exports it"
            if ok
            else "OpenCode 1 can no longer read the store OpenCode 2 opened"
            if MIGRATION_KV_KEY in kv and opened.exit_code == 0
            else "OpenCode 2 left no migration signal in the 1.x store",
            ("store_migration",),
            facts,
        )


__all__ = ["MIGRATION_KV_KEY", "IStoreMigrationProbe", "StoreMigrationProbe", "snapshot"]
