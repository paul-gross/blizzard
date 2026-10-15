"""``FindingStore`` — the finding repository (component tier).

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
from sqlalchemy.exc import OperationalError

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store import batching as batching_module
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.garden.findings.model import FactEntry, Finding, FindingAlreadyExited, FindingExitService
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreError
from blizzard.hub.store.internal.finding_store import FindingStore, lock_findings
from tests.support import count_queries, default_project, default_tenant, hub_store_connections, migrated_hub_at, stamp

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
_LATER = _NOW.replace(hour=13)


def _store_and_engine(tmp_path: Path) -> tuple[FindingStore, Engine]:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migrated_hub_at(db_url)
    engine = create_engine_from_url(db_url)
    with engine.begin() as conn:
        for slug in ("blizzard", "other-scope"):
            conn.execute(sa.insert(s.scopes).values(stamp(s.scopes, slug=slug, description="", created_at=_NOW)))
    return FindingStore(hub_store_connections(engine)), engine


def _store(tmp_path: Path) -> FindingStore:
    store, _ = _store_and_engine(tmp_path)
    return store


def _add(store: FindingStore, project_id: str, finding_id: str = "fin_1", **overrides: object) -> Finding:
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
    return store.add(finding_id, project_id=project_id, **fields)  # type: ignore[arg-type]


def test_add_then_get_round_trips(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)

    added = _add(store, project)

    fetched = store.get("fin_1")
    assert fetched == added
    assert fetched.live is True  # type: ignore[union-attr]
    assert fetched.observed_count == 0  # type: ignore[union-attr]


def test_get_unknown_id_is_none(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get("fin_ghost") is None


def test_get_many_returns_only_the_known_ids_keyed_by_finding_id(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project, finding_id="fin_1")
    _add(store, project, finding_id="fin_2")

    fetched = store.get_many(["fin_1", "fin_ghost", "fin_2"])

    assert set(fetched) == {"fin_1", "fin_2"}
    assert fetched["fin_1"] == store.get("fin_1")
    assert fetched["fin_2"] == store.get("fin_2")


def test_get_many_of_no_ids_is_empty(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.get_many([]) == {}


def test_get_with_facts_returns_the_row_and_its_whole_chain_together(tmp_path: Path) -> None:
    """One read transaction for both, so `get_finding` never disagrees with itself the
    way two independent reads could under a concurrent write."""
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project)
    store.record_fact("fin_1", kind="observed", at=_LATER)

    result = store.get_with_facts("fin_1")

    assert result is not None
    finding, facts = result
    assert finding == store.get("fin_1")
    assert [f.kind for f in facts] == ["add", "observed"]


def test_get_with_facts_of_an_unknown_id_is_none(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.get_with_facts("fin_ghost") is None


def test_record_facts_is_all_or_nothing(tmp_path: Path) -> None:
    """One bad entry in a batch rolls back every entry in it, not just its own."""
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project, finding_id="fin_1")

    with pytest.raises(HubStoreError):
        store.record_facts(
            [
                FactEntry(finding_id="fin_1", kind="observed", at=_LATER, note=None),
                FactEntry(finding_id="fin_1", kind="observed", at=None, note=None),  # type: ignore[arg-type]
            ]
        )

    assert store.get("fin_1").observed_count == 0  # type: ignore[union-attr]


def test_record_facts_writes_nothing_once_a_guarded_finding_moved_off_its_expected_state(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project, finding_id="fin_1")
    _add(store, project, finding_id="fin_2")
    store.record_fact("fin_1", kind="resolved", at=_LATER, note="n", actor="a")

    moved = store.record_facts(
        [
            FactEntry(finding_id="fin_1", kind="wont-fix", at=_LATER, note="n"),
            FactEntry(finding_id="fin_2", kind="wont-fix", at=_LATER, note="n"),
        ],
        expect={"fin_1": "live", "fin_2": "live"},
    )

    assert moved == ["fin_1"]
    assert store.get("fin_1").state == "resolved"  # type: ignore[union-attr]
    assert store.get("fin_2").state == "live"  # type: ignore[union-attr]


def test_record_facts_writes_when_every_guarded_finding_holds_its_expected_state(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project, finding_id="fin_1")

    moved = store.record_facts(
        [FactEntry(finding_id="fin_1", kind="wont-fix", at=_LATER, note="n")], expect={"fin_1": "live"}
    )

    assert moved == []
    assert store.get("fin_1").state == "wont-fix"  # type: ignore[union-attr]


def test_a_stale_second_exit_is_refused_and_records_one_exit(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project, finding_id="fin_1")
    stale = store.get("fin_1")
    assert stale is not None
    service = FindingExitService(repo=store, clock=FixedClock(instant=_LATER))
    service.resolve([stale], note="fixed", actor="a")

    with pytest.raises(FindingAlreadyExited):
        service.resolve([stale], note="fixed again", actor="b")

    _finding, facts = store.get_with_facts("fin_1")  # type: ignore[misc]
    assert [f.kind for f in facts].count("resolved") == 1


def test_a_finding_is_named_by_id_across_two_runs(tmp_path: Path) -> None:
    """The second run's `observed` op names the finding fin_1 recorded first —
    matching is a reference, never a recomputed fingerprint."""
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project)

    store.record_fact("fin_1", kind="observed", at=_LATER)

    fetched = store.get("fin_1")
    assert fetched.observed_count == 1  # type: ignore[union-attr]
    assert fetched.last_seen_at == _LATER  # type: ignore[union-attr]


def test_a_persons_exit_verb_records_no_finding_set(tmp_path: Path) -> None:
    """A human-driven fact belongs to no run — unlike a delivered
    add/observed/gone, it carries no `finding_set_id`."""
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project)

    store.record_fact("fin_1", kind="wont-fix", at=_LATER, actor="pgross")

    with engine.connect() as conn:
        row = conn.execute(sa.text("SELECT finding_set_id FROM finding_facts WHERE kind = 'wont-fix'")).one()
    assert row.finding_set_id is None


def test_a_delivered_facts_actor_reads_back_through_the_real_store(tmp_path: Path) -> None:
    """The `actor` column threaded through `Finding`/`FindingLiveness`
    round-trips through the real store, not just a hand-built domain object — the
    settle-with-original-actor behavior rests on this read, not a fixture."""
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project)

    store.record_fact("fin_1", kind="delivered", at=_LATER, actor="pgross")

    fetched = store.get("fin_1")
    assert fetched.actor == "pgross"  # type: ignore[union-attr]


def test_a_gone_finding_is_excluded_from_list_for_unless_include_gone(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project, finding_id="fin_1")
    _add(store, project, finding_id="fin_2")
    store.record_fact("fin_1", kind="gone", at=_LATER, note="no longer reproduces")

    live = store.list_for(project, "nightly", "blizzard")
    assert [f.finding_id for f in live] == ["fin_2"]

    everything = store.list_for(project, "nightly", "blizzard", include_gone=True)
    assert {f.finding_id for f in everything} == {"fin_1", "fin_2"}


def test_list_for_is_scoped_to_the_named_routine_and_scope(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project, finding_id="fin_1", routine_name="nightly", scope_slug="blizzard")
    _add(store, project, finding_id="fin_2", routine_name="weekly", scope_slug="blizzard")

    assert [f.finding_id for f in store.list_for(project, "nightly", "blizzard")] == ["fin_1"]


def test_list_across_routines_of_no_scope_returns_every_live_finding_across_every_routine(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project, finding_id="fin_1", routine_name="nightly", scope_slug="blizzard")
    _add(store, project, finding_id="fin_2", routine_name="weekly", scope_slug="other-scope")

    assert {f.finding_id for f in store.list_across_routines(project)} == {"fin_1", "fin_2"}


def test_list_across_routines_narrows_to_the_named_scope_across_every_routine(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project, finding_id="fin_1", routine_name="nightly", scope_slug="blizzard")
    _add(store, project, finding_id="fin_2", routine_name="weekly", scope_slug="blizzard")
    _add(store, project, finding_id="fin_3", routine_name="nightly", scope_slug="other-scope")

    assert {f.finding_id for f in store.list_across_routines(project, "blizzard")} == {"fin_1", "fin_2"}


def test_a_gone_finding_is_excluded_from_list_across_routines_unless_include_gone(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project, finding_id="fin_1", routine_name="nightly", scope_slug="blizzard")
    _add(store, project, finding_id="fin_2", routine_name="weekly", scope_slug="other-scope")
    store.record_fact("fin_1", kind="gone", at=_LATER, note="no longer reproduces")

    live = store.list_across_routines(project)
    assert [f.finding_id for f in live] == ["fin_2"]

    everything = store.list_across_routines(project, include_gone=True)
    assert {f.finding_id for f in everything} == {"fin_1", "fin_2"}


def test_list_across_routines_orders_by_finding_id(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project, finding_id="fin_2", routine_name="weekly", scope_slug="other-scope")
    _add(store, project, finding_id="fin_1", routine_name="nightly", scope_slug="blizzard")

    assert [f.finding_id for f in store.list_across_routines(project)] == ["fin_1", "fin_2"]


def test_facts_for_many_batches_across_a_batch_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrinks the batch size so seeding stays cheap, then seeds one more
    finding than two full batches to prove a chain landing in the second (or third)
    batch still comes back correctly via `list_across_routines`, the unbounded caller."""
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    finding_ids = [f"fin_{i}" for i in range(batching_module.BATCH_SIZE + 5)]
    for finding_id in finding_ids:
        _add(store, project, finding_id=finding_id)
        store.record_fact(finding_id, kind="observed", at=_LATER)

    found = {f.finding_id: f for f in store.list_across_routines(project)}

    assert set(found) == set(finding_ids)
    for finding_id in finding_ids:
        assert found[finding_id].observed_count == 1
        assert found[finding_id].last_seen_at == _LATER


def test_get_many_batches_across_a_batch_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`get_many`'s own row read batches through `id_batches` too, not just
    `_facts_for_many` — a chain landing in the second (or third) batch of either query
    must still come back correctly."""
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    finding_ids = [f"fin_{i}" for i in range(batching_module.BATCH_SIZE + 5)]
    for finding_id in finding_ids:
        _add(store, project, finding_id=finding_id)
        store.record_fact(finding_id, kind="observed", at=_LATER)

    found = store.get_many(finding_ids)

    assert set(found) == set(finding_ids)
    for finding_id in finding_ids:
        assert found[finding_id].observed_count == 1
        assert found[finding_id].last_seen_at == _LATER


def test_get_many_query_cost_scales_linearly_with_batch_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each batch's own row and facts reads cost the same regardless of trailing size —
    the per-batch cost is constant, so the total grows with batch count, not id count."""
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    finding_ids = [f"fin_{i}" for i in range(7)]
    for finding_id in finding_ids:
        _add(store, project, finding_id=finding_id)

    one_batch_count = count_queries(engine, lambda: store.get_many(finding_ids[:3]))
    two_batch_count = count_queries(engine, lambda: store.get_many(finding_ids[:6]))
    three_batch_count = count_queries(engine, lambda: store.get_many(finding_ids))

    per_batch_cost = two_batch_count - one_batch_count
    assert per_batch_cost > 0
    assert two_batch_count == one_batch_count + per_batch_cost
    assert three_batch_count == one_batch_count + 2 * per_batch_cost  # the trailing size-1 batch costs the same


def test_count_by_class_counts_across_the_named_routine(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project, finding_id="fin_1", class_="stale-docstring")
    _add(store, project, finding_id="fin_2", class_="stale-docstring")
    _add(store, project, finding_id="fin_3", class_="dead-code")

    assert store.count_by_class(project, "nightly", "stale-docstring") == 2
    assert store.count_by_class(project, "nightly", "dead-code") == 1
    assert store.count_by_class(project, "nightly", "unseen-class") == 0


def _add_review_finding(engine: Engine, finding_id: str = "fin_review", *, scope_slug: str = "blizzard") -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.insert(s.findings).values(
                stamp(
                    s.findings,
                    finding_id=finding_id,
                    routine_name=None,
                    scope_slug=scope_slug,
                    class_="correctness",
                    locus="a.py:1",
                    summary="s",
                    source="review",
                    severity="should-fix",
                    raised_by_chunk_id="ch_1",
                )
            )
        )
        conn.execute(
            sa.insert(s.finding_facts).values(
                stamp(s.finding_facts, finding_id=finding_id, kind="add", recorded_at=_NOW)
            )
        )


def test_list_by_source_narrows_to_review_findings_under_one_scope(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project, finding_id="fin_routine")
    _add_review_finding(engine, "fin_review")
    _add_review_finding(engine, "fin_review_other_scope", scope_slug="other-scope")

    result = store.list_by_source(project, scope_slug="blizzard", source="review")

    assert [f.finding_id for f in result] == ["fin_review"]
    assert result[0].source == "review"
    assert result[0].severity == "should-fix"
    assert result[0].raised_by_chunk_id == "ch_1"
    assert result[0].routine_name is None


def test_list_by_source_excludes_a_gone_finding_unless_include_gone(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add_review_finding(engine, "fin_review")
    store.record_fact("fin_review", kind="gone", at=_LATER, note="no longer reproduces")

    assert store.list_by_source(project, scope_slug="blizzard", source="review") == []
    everything = store.list_by_source(project, scope_slug="blizzard", source="review", include_gone=True)
    assert [f.finding_id for f in everything] == ["fin_review"]


def test_list_by_source_query_plans_as_an_index_search(tmp_path: Path) -> None:
    _store, engine = _store_and_engine(tmp_path)
    _add_review_finding(engine)

    with engine.connect() as conn:
        plan = conn.execute(
            sa.text(
                "EXPLAIN QUERY PLAN SELECT * FROM findings "
                "WHERE tenant_id = :tenant AND project_id = :project AND scope_slug = 'blizzard' AND source = 'review'"
            ),
            {"tenant": default_tenant(engine), "project": default_project(engine)},
        ).all()
    assert any("ix_findings_tenant_project_scope_source" in str(row) for row in plan), plan


def test_list_for_query_plans_as_an_index_search(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project)

    with engine.connect() as conn:
        plan = conn.execute(
            sa.text(
                "EXPLAIN QUERY PLAN SELECT * FROM findings "
                "WHERE tenant_id = :tenant AND project_id = :project AND routine_name = 'nightly' AND scope_slug = 'blizzard'"
            ),
            {"tenant": default_tenant(engine), "project": default_project(engine)},
        ).all()
    assert any("ix_findings_tenant_project_routine_scope" in str(row) for row in plan), plan


def test_count_by_class_query_plans_as_an_index_search(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project)

    with engine.connect() as conn:
        plan = conn.execute(
            sa.text(
                "EXPLAIN QUERY PLAN SELECT COUNT(*) FROM findings "
                "WHERE tenant_id = :tenant AND project_id = :project AND routine_name = 'nightly' AND class = 'stale-docstring'"
            ),
            {"tenant": default_tenant(engine), "project": default_project(engine)},
        ).all()
    assert any("ix_findings_tenant_project_routine_class" in str(row) for row in plan), plan


def test_a_guarded_finding_write_holds_the_writer_lock_until_its_transaction_ends(tmp_path: Path) -> None:
    store, engine = _store_and_engine(tmp_path)
    project = default_project(engine)
    _add(store, project)

    connections = hub_store_connections(engine)
    with connections.read("winner") as winner, connections.read("loser") as loser:
        lock_findings(winner, ["fin_1"])
        loser.exec_driver_sql("PRAGMA busy_timeout=0")
        with pytest.raises(OperationalError, match="locked"):
            lock_findings(loser, ["fin_1"])
        loser.rollback()
        winner.commit()

    entry = FactEntry(finding_id="fin_1", kind="resolved", at=_LATER, note="done")
    assert store.record_facts([entry], expect={"fin_1": "live"}) == []
    assert store.record_facts([entry], expect={"fin_1": "live"}) == ["fin_1"]
