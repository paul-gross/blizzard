"""The newest-lifecycle-fact retirement reads — newest by fact ``id``, never by ``set_at`` — driven over a
head-migrated store (unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, insert

from blizzard.hub.domain.config.repositories import RepositoryId
from blizzard.hub.domain.config.work_sources import WorkSourceId
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.newest_fact import newest_fact_select
from blizzard.hub.store.internal.repository_record_store import _retirements  # type: ignore[attr-defined]
from blizzard.hub.store.internal.work_source_record_store import _retired_ids  # type: ignore[attr-defined]
from blizzard.hub.store.scoped import statements_of
from tests.support import hub_store_connections, migrate_to, stamp

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)

#: Per key, facts in insertion (``id``) order as ``(retired, set_at offset in days)``; ``set_at`` runs
#: against ``id``, so a read ordering by ``set_at`` — or taking the oldest id — answers differently.
_HISTORY = {
    "k1": [(False, 9), (True, 3)],
    "k2": [(True, 9), (False, 3)],
    "k3": [(False, 8), (True, 2)],
}


def _engine(tmp_path: Path) -> Engine:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        for key, facts in _HISTORY.items():
            for retired, days in facts:
                common = {"retired": retired, "set_at": _T0 + timedelta(days=days), "set_by": "op"}
                conn.execute(
                    insert(s.repository_lifecycle_facts).values(
                        stamp(s.repository_lifecycle_facts, repository_id=key, **common)
                    )
                )
                conn.execute(
                    insert(s.work_source_lifecycle_facts).values(
                        stamp(s.work_source_lifecycle_facts, work_source_id=key, **common)
                    )
                )
    return engine


def _read(engine: Engine, fn):  # type: ignore[no-untyped-def]
    with hub_store_connections(engine).read("test") as conn:
        return fn(conn)


def test_repository_retirements_carry_the_newest_facts_set_at_for_every_retired_key(tmp_path: Path) -> None:
    engine = _engine(tmp_path)

    assert _read(engine, lambda conn: _retirements(conn)) == {
        "k1": _T0 + timedelta(days=3),
        "k3": _T0 + timedelta(days=2),
    }


def test_repository_retirements_bounded_to_ids_read_only_those_ids(tmp_path: Path) -> None:
    engine = _engine(tmp_path)

    answer = _read(engine, lambda conn: _retirements(conn, [RepositoryId("k1"), RepositoryId("k2")]))

    assert answer == {"k1": _T0 + timedelta(days=3)}


def test_work_source_retired_ids_follow_the_newest_fact_by_id(tmp_path: Path) -> None:
    engine = _engine(tmp_path)

    assert _read(engine, lambda conn: _retired_ids(conn)) == {"k1", "k3"}


def test_work_source_retired_ids_bounded_to_ids_read_only_those_ids(tmp_path: Path) -> None:
    engine = _engine(tmp_path)

    assert _read(engine, lambda conn: _retired_ids(conn, [WorkSourceId("k2"), WorkSourceId("k3")])) == {"k3"}


def test_newest_fact_select_returns_each_keys_highest_id_row_whatever_its_retirement(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    facts = s.repository_lifecycle_facts

    def read(conn):  # type: ignore[no-untyped-def]
        stmt = newest_fact_select(statements_of(conn), facts, facts.c.repository_id, ["k2", "k3"], facts.c.set_at)
        return {r.repository_id: r.set_at for r in conn.execute(stmt.add_columns(facts.c.repository_id))}

    assert _read(engine, read) == {"k2": _T0 + timedelta(days=3), "k3": _T0 + timedelta(days=2)}
