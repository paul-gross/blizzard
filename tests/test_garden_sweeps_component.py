"""``GardenSweepsService`` (component tier) — a routine's last-swept table and
measurement series, wired with real ``GardenSweepsStore``, ``ScopeStore``, and
``RoutineScopeStore`` collaborators over sqlite-on-disk, doubles only at the clock.
Proves the D1/D3 declared-set-coverage rule end to end: real scopes, real routine_scopes
links, real lifecycle facts, real ``finding_sets``/``artifacts`` rows."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import insert

from blizzard.hub.domain.garden_sweeps import GardenSweepsService
from blizzard.hub.domain.routines import Routine
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.garden_sweeps_store import GardenSweepsStore
from blizzard.hub.store.internal.routine_scope_store import RoutineScopeStore
from blizzard.hub.store.internal.scope_store import ScopeStore
from tests.support import hub_store_connections, migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
_SINCE = datetime(2026, 1, 1, tzinfo=UTC)
_UNTIL = datetime(2026, 1, 15, tzinfo=UTC)


def _routine(*, routine_id: str = "rtn_1", default_scope_slug: str = "blizzard") -> Routine:
    return Routine(
        routine_id=routine_id,
        name="nightly",
        graph_name="alpha",
        default_scope_slug=default_scope_slug,
        created_at=_NOW,
    )


def _seed_routine(conn: sa.Connection, routine: Routine) -> None:
    conn.execute(
        insert(s.routines).values(
            routine_id=routine.routine_id,
            name=routine.name,
            graph_name=routine.graph_name,
            default_scope_slug=routine.default_scope_slug,
            default_model=json.dumps([]),
            default_effort=None,
            created_at=routine.created_at,
        )
    )


def _link(conn: sa.Connection, routine_id: str, scope_slug: str) -> None:
    conn.execute(insert(s.routine_scopes).values(routine_id=routine_id, scope_slug=scope_slug))


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
    return GardenSweepsService(
        repo=GardenSweepsStore(connections),
        scopes=ScopeStore(connections),
        routine_scopes=RoutineScopeStore(connections),
    )


def _seed_common(conn: sa.Connection, routine: Routine) -> None:
    seed_graph(conn, "gr_1", at=_NOW)
    seed_chunk(conn, "ch_1", graph_id="gr_1", at=_NOW)
    conn.execute(insert(s.scopes).values(slug="blizzard", description="", created_at=_NOW))
    _seed_routine(conn, routine)
    _link(conn, routine.routine_id, "blizzard")


def test_a_scope_with_no_finding_set_reads_never(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    routine = _routine()
    with engine.begin() as conn:
        _seed_common(conn, routine)
    service = _service(engine)

    sweeps = service.sweeps(routine, since=_SINCE, until=_UNTIL)

    (row,) = sweeps.last_swept
    assert row.scope_slug == "blizzard"
    assert row.finding_set_id is None


def test_the_newest_set_by_produced_at_is_reported(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    routine = _routine()
    with engine.begin() as conn:
        _seed_common(conn, routine)
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

    sweeps = service.sweeps(routine, since=_SINCE, until=_UNTIL)

    (row,) = sweeps.last_swept
    assert row.finding_set_id == "fins_2"
    assert row.produced_at == datetime(2026, 1, 9, tzinfo=UTC)
    assert row.revisions == {"blizzard": "bbb"}


def test_last_swept_covers_the_declared_set_scoped_by_link_and_retirement(tmp_path: Path) -> None:
    """D1/D3: a linked, non-retired scope never swept reads `never`; an unlinked scope
    is omitted regardless of retirement or of its own sweep history; a linked, retired
    scope keeps its row if this routine swept it, but is omitted if it never was."""
    _, engine = migrate_to(tmp_path, "head")
    routine = _routine(default_scope_slug="linked-never-swept")
    linked_slugs = ("linked-never-swept", "linked-retired-swept", "linked-retired-never-swept")
    retired_slugs = ("linked-retired-swept", "linked-retired-never-swept", "not-linked-retired")
    all_slugs = (*linked_slugs, "not-linked", "not-linked-swept", "not-linked-retired")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_NOW)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=_NOW)
        for slug in all_slugs:
            conn.execute(insert(s.scopes).values(slug=slug, description="", created_at=_NOW))
        for slug in retired_slugs:
            conn.execute(
                insert(s.scope_lifecycle_facts).values(slug=slug, retired=True, set_at=_NOW, set_by="operator")
            )
        _seed_routine(conn, routine)
        for slug in linked_slugs:
            _link(conn, routine.routine_id, slug)
        for artifact_id, finding_set_id, scope_slug in (
            ("art_1", "fins_1", "linked-retired-swept"),
            # Swept while this routine still had it linked, but unlinked before this
            # read — a stale fact must not resurface a scope no longer in the set.
            ("art_2", "fins_2", "not-linked-swept"),
        ):
            _seed_artifact(conn, artifact_id, chunk_id="ch_1", produced_at=datetime(2026, 1, 2, tzinfo=UTC))
            _seed_finding_set(
                conn,
                finding_set_id,
                artifact_id=artifact_id,
                chunk_id="ch_1",
                scope_slug=scope_slug,
                routine_name="nightly",
                revisions={},
                measurement=None,
            )
    service = _service(engine)

    sweeps = service.sweeps(routine, since=_SINCE, until=_UNTIL)

    assert {row.scope_slug for row in sweeps.last_swept} == {"linked-never-swept", "linked-retired-swept"}


def test_the_measurement_series_is_windowed_while_last_swept_is_not(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    routine = _routine()
    with engine.begin() as conn:
        _seed_common(conn, routine)
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

    sweeps = service.sweeps(routine, since=_SINCE, until=_UNTIL)

    assert sweeps.measurements == []
    (row,) = sweeps.last_swept
    assert row.finding_set_id == "fins_old"
    assert row.produced_at == datetime(2025, 1, 1, tzinfo=UTC)
