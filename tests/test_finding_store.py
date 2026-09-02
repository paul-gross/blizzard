"""``FindingStore`` — the finding repository (blizzard#390, component tier).

Migrated-to-head sqlite-on-disk — the ``tests/test_scope_store.py`` shape. Proves a
finding named by id across two runs, and the two indexed reads (`list_for`'s
routine+scope bucket, `count_by_class`'s routine+class recurrence) plan as an index
search rather than a table scan."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.findings import FactEntry, Finding
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.errors import HubStoreError
from blizzard.hub.store.internal.finding_store import FindingStore
from tests.support import hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
_LATER = _NOW.replace(hour=13)


def _store_and_engine(tmp_path: Path) -> tuple[FindingStore, Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO scopes (slug, description, created_at) VALUES ('blizzard', '', :now)"), {"now": _NOW}
        )
    return FindingStore(hub_store_connections(engine)), engine


def _store(tmp_path: Path) -> FindingStore:
    store, _ = _store_and_engine(tmp_path)
    return store


def _add(store: FindingStore, finding_id: str = "fin_1", **overrides: object) -> Finding:
    fields: dict[str, object] = {
        "routine_name": "nightly",
        "scope_slug": "blizzard",
        "class_": "stale-docstring",
        "locus": "src/billing/invoice.py:42",
        "summary": "Module docstring narrates the change history rather than the contract.",
        "introduced": "a1b2c3d",
        "at": _NOW,
    }
    fields.update(overrides)
    return store.add(finding_id, **fields)  # type: ignore[arg-type]


def test_add_then_get_round_trips(tmp_path: Path) -> None:
    store = _store(tmp_path)

    added = _add(store)

    fetched = store.get("fin_1")
    assert fetched == added
    assert fetched.live is True  # type: ignore[union-attr]
    assert fetched.observed_count == 0  # type: ignore[union-attr]


def test_get_unknown_id_is_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get("fin_ghost") is None


def test_get_many_returns_only_the_known_ids_keyed_by_finding_id(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add(store, finding_id="fin_1")
    _add(store, finding_id="fin_2")

    fetched = store.get_many(["fin_1", "fin_ghost", "fin_2"])

    assert set(fetched) == {"fin_1", "fin_2"}
    assert fetched["fin_1"] == store.get("fin_1")
    assert fetched["fin_2"] == store.get("fin_2")


def test_get_many_of_no_ids_is_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.get_many([]) == {}


def test_record_facts_is_all_or_nothing(tmp_path: Path) -> None:
    """Pins D7: one bad entry in a batch rolls back every entry in it, not just its own."""
    store = _store(tmp_path)
    _add(store, finding_id="fin_1")

    with pytest.raises(HubStoreError):
        store.record_facts(
            [
                FactEntry(finding_id="fin_1", kind="observed", at=_LATER, note=None),
                FactEntry(finding_id="fin_1", kind="observed", at=None, note=None),  # type: ignore[arg-type]
            ]
        )

    assert store.get("fin_1").observed_count == 0  # type: ignore[union-attr]


def test_a_finding_is_named_by_id_across_two_runs(tmp_path: Path) -> None:
    """The second run's `observed` op names the finding fin_1 recorded first (D2) —
    matching is a reference, never a recomputed fingerprint."""
    store = _store(tmp_path)
    _add(store)

    store.record_fact("fin_1", kind="observed", at=_LATER)

    fetched = store.get("fin_1")
    assert fetched.observed_count == 1  # type: ignore[union-attr]
    assert fetched.last_seen_at == _LATER  # type: ignore[union-attr]


def test_a_persons_exit_verb_records_no_finding_set(tmp_path: Path) -> None:
    """A human-driven fact belongs to no run (blizzard#396 D1) — unlike a delivered
    add/observed/gone, it carries no `finding_set_id`."""
    store, engine = _store_and_engine(tmp_path)
    _add(store)

    store.record_fact("fin_1", kind="wont-fix", at=_LATER, actor="pgross")

    with engine.connect() as conn:
        row = conn.execute(sa.text("SELECT finding_set_id FROM finding_facts WHERE kind = 'wont-fix'")).one()
    assert row.finding_set_id is None


def test_a_gone_finding_is_excluded_from_list_for_unless_include_gone(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add(store, finding_id="fin_1")
    _add(store, finding_id="fin_2")
    store.record_fact("fin_1", kind="gone", at=_LATER, note="no longer reproduces")

    live = store.list_for("nightly", "blizzard")
    assert [f.finding_id for f in live] == ["fin_2"]

    everything = store.list_for("nightly", "blizzard", include_gone=True)
    assert {f.finding_id for f in everything} == {"fin_1", "fin_2"}


def test_list_for_is_scoped_to_the_named_routine_and_scope(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add(store, finding_id="fin_1", routine_name="nightly", scope_slug="blizzard")
    _add(store, finding_id="fin_2", routine_name="weekly", scope_slug="blizzard")

    assert [f.finding_id for f in store.list_for("nightly", "blizzard")] == ["fin_1"]


def test_count_by_class_counts_across_the_named_routine(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _add(store, finding_id="fin_1", class_="stale-docstring")
    _add(store, finding_id="fin_2", class_="stale-docstring")
    _add(store, finding_id="fin_3", class_="dead-code")

    assert store.count_by_class("nightly", "stale-docstring") == 2
    assert store.count_by_class("nightly", "dead-code") == 1
    assert store.count_by_class("nightly", "unseen-class") == 0


def test_list_for_query_plans_as_an_index_search(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    _add(store)

    with engine.connect() as conn:
        plan = conn.execute(
            sa.text(
                "EXPLAIN QUERY PLAN SELECT * FROM findings WHERE routine_name = 'nightly' AND scope_slug = 'blizzard'"
            )
        ).all()
    assert any("ix_findings_routine_scope" in str(row) for row in plan), plan


def test_count_by_class_query_plans_as_an_index_search(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    _add(store)

    with engine.connect() as conn:
        plan = conn.execute(
            sa.text(
                "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM findings WHERE routine_name = 'nightly' AND class = 'stale-docstring'"
            )
        ).all()
    assert any("ix_findings_routine_class" in str(row) for row in plan), plan
