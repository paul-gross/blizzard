"""``FindingStore.list_page``'s SQL liveness prefilter (component tier).

``bzh:page-bounded-read`` / ``bzh:live-set-read`` — the live set the query keeps equals
``derive_liveness``'s for every fact shape, and the page's cost stays flat when only the
exited-finding count grows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine, insert

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.garden.findings.model import FACT_KINDS, FindingFact, derive_liveness
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from tests.support import count_queries, count_rows_read, hub_store_connections

pytestmark = pytest.mark.component

_T0 = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _store_and_engine(tmp_path: Path) -> tuple[FindingStore, Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO scopes (slug, description, created_at) VALUES ('blizzard', '', :now)"), {"now": _T0}
        )
    return FindingStore(hub_store_connections(engine)), engine


def _finding(engine: Engine, finding_id: str, facts: list[tuple[str, int]]) -> None:
    """Insert a finding and raw facts as ``(kind, recorded_at seconds)``, in insertion order."""
    with engine.begin() as conn:
        conn.execute(
            insert(s.findings).values(
                finding_id=finding_id,
                routine_name="nightly",
                scope_slug="blizzard",
                class_="c",
                locus="l",
                summary="x",
                introduced=None,
                introduced_at=_T0,
                source="routine",
            )
        )
        for kind, seconds in facts:
            conn.execute(insert(s.finding_facts).values(finding_id=finding_id, kind=kind, recorded_at=_at(seconds)))


def _fixtures() -> dict[str, list[tuple[str, int]]]:
    fixtures: dict[str, list[tuple[str, int]]] = {}
    for kind in sorted(FACT_KINDS):
        fixtures[f"newest_{kind}"] = [("add", 0), (kind, 10)]
    fixtures["exit_then_reopened"] = [("add", 0), ("wont-fix", 5), ("reopened", 10)]
    fixtures["gone_then_observed"] = [("add", 0), ("gone", 5), ("observed", 10)]
    fixtures["delivered_then_observed"] = [("add", 0), ("delivered", 5), ("observed", 10)]
    fixtures["tie_later_inserted_exit"] = [("add", 0), ("observed", 5), ("gone", 5)]
    fixtures["tie_later_inserted_live"] = [("add", 0), ("gone", 5), ("observed", 5)]
    fixtures["out_of_order_older_exit_loses"] = [("add", 0), ("observed", 10), ("resolved", 5)]
    fixtures["out_of_order_older_live_loses"] = [("add", 10), ("resolved", 20), ("observed", 5)]
    fixtures["no_facts"] = []
    return fixtures


def test_sql_live_set_equals_derive_liveness(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    fixtures = _fixtures()
    for finding_id, facts in fixtures.items():
        _finding(engine, finding_id, facts)

    page = store.list_page(routine_name=None, scope_slug=None, include_gone=False, limit=len(fixtures) + 1)
    everything = store.list_page(routine_name=None, scope_slug=None, include_gone=True, limit=len(fixtures) + 1)

    assert page.next_cursor is None
    assert len(everything.findings) == len(fixtures)
    expected = [f.finding_id for f in everything.findings if f.live]
    assert [f.finding_id for f in page.findings] == expected
    assert {f.finding_id for f in everything.findings if f.live} == {
        fid for fid, facts in fixtures.items() if not facts or derive_liveness(_as_facts(facts)).live
    }
    assert "no_facts" in expected
    assert "out_of_order_older_exit_loses" in expected
    assert "tie_later_inserted_exit" not in expected


def _as_facts(facts: list[tuple[str, int]]):  # type: ignore[no-untyped-def]
    return [FindingFact(kind=kind, recorded_at=_at(seconds)) for kind, seconds in facts]


def _seed_interleaved(engine: Engine, *, exited_each_gap: int) -> None:
    for live_index in range(4):
        for gap in range(exited_each_gap):
            _finding(engine, f"fin_{live_index}_{gap}_x", [("add", 0), ("wont-fix", 1)])
        _finding(engine, f"fin_{live_index}_live", [("add", 0)])
    for gap in range(exited_each_gap):
        _finding(engine, f"fin_9_{gap}_x", [("add", 0), ("wont-fix", 1)])


def test_page_cost_is_flat_in_exited_finding_count(tmp_path: Path) -> None:
    measured = []
    for gaps in (1, 12):
        (tmp_path / f"g{gaps}").mkdir()
        store, engine = _store_and_engine(tmp_path / f"g{gaps}")
        _seed_interleaved(engine, exited_each_gap=gaps)

        def read(store: FindingStore = store):  # type: ignore[no-untyped-def]
            return store.list_page(routine_name="nightly", scope_slug="blizzard", limit=2)

        first = read()
        measured.append(
            (
                count_queries(engine, read),
                count_rows_read(engine, read),
                [f.finding_id.split("_")[1] for f in first.findings],
                first.next_cursor is not None,
            )
        )
    assert measured[0][0] == measured[1][0]
    assert measured[0][1] == measured[1][1]
    assert measured[0][2:] == measured[1][2:]
