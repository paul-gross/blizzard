"""``GardenSweepsService`` (component tier) — a routine's last-swept table and
measurement series, wired with real ``GardenSweepsStore`` and ``ScopeStore`` collaborators
over sqlite-on-disk, doubles only at the clock. Proves the D3 scope-coverage rule end to
end: real scopes, real lifecycle facts, real ``finding_sets``/``artifacts`` rows."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import insert

from blizzard.hub.domain.garden_sweeps import GardenSweepsService
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.garden_sweeps_store import GardenSweepsStore
from blizzard.hub.store.internal.scope_store import ScopeStore
from tests.support import hub_store_connections, migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
_SINCE = datetime(2026, 1, 1, tzinfo=UTC)
_UNTIL = datetime(2026, 1, 15, tzinfo=UTC)


def _seed_artifact(conn: sa.Connection, artifact_id: str, *, chunk_id: str, produced_at: datetime) -> None:
    conn.execute(
        insert(s.artifacts).values(
            artifact_id=artifact_id,
            chunk_id=chunk_id,
            node_id="nd_1",
            node_name="survey",
            epoch=1,
            name="findings",
            kind="asset",
            data="[]",
            produced_at=produced_at,
        )
    )


def _seed_finding_set(
    conn: sa.Connection,
    finding_set_id: str,
    *,
    artifact_id: str,
    chunk_id: str,
    scope_slug: str,
    routine_name: str,
    revisions: dict[str, str],
    measurement: str | None,
) -> None:
    conn.execute(
        insert(s.finding_sets).values(
            finding_set_id=finding_set_id,
            artifact_id=artifact_id,
            chunk_id=chunk_id,
            scope_slug=scope_slug,
            routine_name=routine_name,
            revisions=json.dumps(revisions),
            measurement=measurement,
        )
    )


def _service(engine: sa.Engine) -> GardenSweepsService:
    connections = hub_store_connections(engine)
    return GardenSweepsService(repo=GardenSweepsStore(connections), scopes=ScopeStore(connections))


def _seed_common(conn: sa.Connection) -> None:
    seed_graph(conn, "gr_1", at=_NOW)
    seed_chunk(conn, "ch_1", graph_id="gr_1", at=_NOW)
    conn.execute(insert(s.scopes).values(slug="blizzard", description="", created_at=_NOW))


def test_a_scope_with_no_finding_set_reads_never(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        _seed_common(conn)
    service = _service(engine)

    sweeps = service.sweeps("nightly", since=_SINCE, until=_UNTIL)

    (row,) = sweeps.last_swept
    assert row.scope_slug == "blizzard"
    assert row.finding_set_id is None


def test_the_newest_set_by_produced_at_is_reported(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        _seed_common(conn)
        _seed_artifact(conn, "art_1", chunk_id="ch_1", produced_at=datetime(2026, 1, 2, tzinfo=UTC))
        _seed_artifact(conn, "art_2", chunk_id="ch_1", produced_at=datetime(2026, 1, 9, tzinfo=UTC))
        _seed_finding_set(
            conn,
            "fins_1",
            artifact_id="art_1",
            chunk_id="ch_1",
            scope_slug="blizzard",
            routine_name="nightly",
            revisions={"blizzard": "aaa"},
            measurement=None,
        )
        _seed_finding_set(
            conn,
            "fins_2",
            artifact_id="art_2",
            chunk_id="ch_1",
            scope_slug="blizzard",
            routine_name="nightly",
            revisions={"blizzard": "bbb"},
            measurement=None,
        )
    service = _service(engine)

    sweeps = service.sweeps("nightly", since=_SINCE, until=_UNTIL)

    (row,) = sweeps.last_swept
    assert row.finding_set_id == "fins_2"
    assert row.produced_at == datetime(2026, 1, 9, tzinfo=UTC)
    assert row.revisions == {"blizzard": "bbb"}


def test_a_retired_scope_this_routine_swept_is_still_listed_but_a_never_swept_retired_scope_is_not(
    tmp_path: Path,
) -> None:
    """D3: every non-retired scope, plus any *retired* scope this routine has swept —
    a retired scope with no sweep of its own must not surface a phantom never row."""
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_NOW)
        conn.execute(insert(s.scopes).values(slug="live", description="", created_at=_NOW))
        conn.execute(insert(s.scopes).values(slug="swept-then-retired", description="", created_at=_NOW))
        conn.execute(insert(s.scopes).values(slug="retired-untouched", description="", created_at=_NOW))
        for slug in ("swept-then-retired", "retired-untouched"):
            conn.execute(
                insert(s.scope_lifecycle_facts).values(slug=slug, retired=True, set_at=_NOW, set_by="operator")
            )
        _seed_artifact(conn, "art_1", chunk_id="ch_1", produced_at=datetime(2026, 1, 2, tzinfo=UTC))
        _seed_finding_set(
            conn,
            "fins_1",
            artifact_id="art_1",
            chunk_id="ch_1",
            scope_slug="swept-then-retired",
            routine_name="nightly",
            revisions={},
            measurement=None,
        )
    service = _service(engine)

    sweeps = service.sweeps("nightly", since=_SINCE, until=_UNTIL)

    assert {row.scope_slug for row in sweeps.last_swept} == {"live", "swept-then-retired"}


def test_the_measurement_series_is_windowed_while_last_swept_is_not(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        _seed_common(conn)
        _seed_artifact(conn, "art_old", chunk_id="ch_1", produced_at=datetime(2025, 1, 1, tzinfo=UTC))
        _seed_finding_set(
            conn,
            "fins_old",
            artifact_id="art_old",
            chunk_id="ch_1",
            scope_slug="blizzard",
            routine_name="nightly",
            revisions={},
            measurement="a sweep from long ago",
        )
    service = _service(engine)

    sweeps = service.sweeps("nightly", since=_SINCE, until=_UNTIL)

    assert sweeps.measurements == []
    (row,) = sweeps.last_swept
    assert row.finding_set_id == "fins_old"
    assert row.produced_at == datetime(2025, 1, 1, tzinfo=UTC)
