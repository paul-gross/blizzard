"""OpenCode 1 refuses a store OpenCode 2 has migrated beneath it, by name, read without writing or locking it."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.health import HarnessHealthCause
from blizzard.runner.harness.health_cache import HarnessHealthCache
from blizzard.runner.harness.internal.committed_corpus import CommittedCorpus
from blizzard.runner.harness.offline_compatibility import admitted_corpus_versions
from blizzard.runner.harness.opencode.compatibility.probe import ADMITTED_OPENCODE_RANGE
from blizzard.runner.harness.opencode.health import MIGRATION_KV_KEY, OpenCodeHealthProbe, store_migrated
from tests.support.child_launch import version_probe

_ADMITTED = admitted_corpus_versions(CommittedCorpus(), "opencode", ADMITTED_OPENCODE_RANGE)[0]


def _store(path: Path, *, kv: dict[str, str] | None) -> Path:
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("create table session (id text primary key)")
        if kv is not None:
            db.execute("create table kv (key text primary key, value text)")
            db.executemany("insert into kv values (?, ?)", kv.items())
    return path


def test_a_store_marked_by_the_migration_is_migrated(tmp_path: Path) -> None:
    store = _store(tmp_path / "opencode.db", kv={MIGRATION_KV_KEY: '{"phase":"completed"}'})
    before = store.read_bytes()
    assert store_migrated(store)
    assert store.read_bytes() == before


def test_a_store_without_the_mark_is_not_migrated(tmp_path: Path) -> None:
    assert not store_migrated(_store(tmp_path / "plain.db", kv=None))
    assert not store_migrated(_store(tmp_path / "other-kv.db", kv={"other": "1"}))
    assert not store_migrated(tmp_path / "absent.db")
    garbage = tmp_path / "garbage.db"
    garbage.write_text("not a database")
    assert not store_migrated(garbage)


def test_a_migrated_store_leaves_opencode_1_unavailable_naming_the_store(tmp_path: Path) -> None:
    store = _store(tmp_path / "opencode.db", kv={MIGRATION_KV_KEY: '{"phase":"completed"}'})
    auth = tmp_path / "auth.json"
    auth.write_text('{"openai": {}}')
    binary = tmp_path / "opencode"
    binary.write_text(f"#!/bin/sh\necho '{_ADMITTED}'\n")
    binary.chmod(0o755)
    probe = OpenCodeHealthProbe(
        str(binary), auth_path=auth, corpus=CommittedCorpus(), versions=version_probe(), store_path=store
    )
    (conflict,) = probe.config_conflicts()
    assert str(store) in conflict and MIGRATION_KV_KEY in conflict
    cache = HarnessHealthCache(
        clock=FixedClock(datetime(2026, 11, 6, tzinfo=UTC)),
        probes={"opencode": probe},
        corpus=CommittedCorpus(),
        selftest_results=None,
    )
    result = cache.refresh("opencode", adapter=_Tiers(), observed_version=_ADMITTED)
    assert result is not None and not result.available and result.cause is HarnessHealthCause.CONFIG_CONFLICT


def test_an_unmigrated_store_raises_no_conflict(tmp_path: Path) -> None:
    probe = OpenCodeHealthProbe(
        "opencode",
        auth_path=None,
        corpus=CommittedCorpus(),
        versions=version_probe(),
        store_path=_store(tmp_path / "opencode.db", kv=None),
    )
    assert probe.config_conflicts() == ()


class _Tiers:
    def resolve_model_strict(self, preferences: object) -> str | None:
        return "native"
