"""``FindingSetStore`` — the finding-set repository (blizzard#390, component tier).

Migrated-to-head sqlite-on-disk. Proves three sets from one run (one chunk) are
distinguished by their artifacts (D6) — one row per delivered list."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, insert
from sqlalchemy.exc import IntegrityError

from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingSetStore
from tests.support import migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _seed_artifact(conn: sa.Connection, artifact_id: str, *, chunk_id: str, name: str) -> None:
    conn.execute(
        insert(s.artifacts).values(
            artifact_id=artifact_id,
            chunk_id=chunk_id,
            node_id="nd_1",
            node_name="survey",
            epoch=1,
            name=name,
            kind="asset",
            data="[]",
            produced_at=_NOW,
        )
    )


def _store(tmp_path: Path) -> tuple[FindingSetStore, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_NOW)
        conn.execute(insert(s.scopes).values(slug="blizzard", description="", created_at=_NOW))
        _seed_artifact(conn, "art_1", chunk_id="ch_1", name="findings-billing")
        _seed_artifact(conn, "art_2", chunk_id="ch_1", name="findings-auth")
        _seed_artifact(conn, "art_3", chunk_id="ch_1", name="findings-web")
    return FindingSetStore(engine), engine


def test_create_then_get_round_trips(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)

    created = store.create(
        "fins_1",
        artifact_id="art_1",
        chunk_id="ch_1",
        scope_slug="blizzard",
        revisions={"blizzard": "a1b2c3d"},
        measurement="23 files checked",
    )

    assert store.get("fins_1") == created


def test_get_unknown_id_is_none(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    assert store.get("fins_ghost") is None


def test_three_sets_from_one_run_are_distinguished_by_their_artifacts(tmp_path: Path) -> None:
    """A fanned-out graph's three delivered lists mint three sets, all naming the same
    run (chunk_id), kept apart by their distinct artifact_id (D6)."""
    store, _ = _store(tmp_path)

    store.create("fins_1", artifact_id="art_1", chunk_id="ch_1", scope_slug="blizzard", revisions={}, measurement=None)
    store.create("fins_2", artifact_id="art_2", chunk_id="ch_1", scope_slug="blizzard", revisions={}, measurement=None)
    store.create("fins_3", artifact_id="art_3", chunk_id="ch_1", scope_slug="blizzard", revisions={}, measurement=None)

    sets = store.list_for_chunk("ch_1")

    assert {s.finding_set_id for s in sets} == {"fins_1", "fins_2", "fins_3"}
    assert {s.artifact_id for s in sets} == {"art_1", "art_2", "art_3"}


def test_a_second_set_on_the_same_artifact_is_refused(tmp_path: Path) -> None:
    """One set per delivered list (D6) — the unique FK on `artifact_id` is the backstop."""
    store, _ = _store(tmp_path)
    store.create("fins_1", artifact_id="art_1", chunk_id="ch_1", scope_slug="blizzard", revisions={}, measurement=None)

    with pytest.raises(IntegrityError):
        store.create(
            "fins_2", artifact_id="art_1", chunk_id="ch_1", scope_slug="blizzard", revisions={}, measurement=None
        )
