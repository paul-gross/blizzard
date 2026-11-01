"""Upgrade old terminal transitions, while continuing to detect new missed enqueues."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.hub.store import schema as s
from blizzard.tools.invariants import hub_invariants
from tests.support import migrate_to, stamp

pytestmark = [pytest.mark.component]

_PARENT = "20261029_1200_landing_policies"
_REVISION = "20261031_1800_terminal_close"
_AT = datetime(2026, 1, 1, tzinfo=UTC)


def _terminal(conn: sa.Connection, chunk: str) -> None:
    conn.execute(sa.insert(s.chunks).values(stamp(s.chunks, chunk_id=chunk, graph_id="gr_1", minted_at=_AT)))
    conn.execute(
        sa.insert(s.transitions).values(
            stamp(
                s.transitions,
                transition_id=f"tr_{chunk}",
                chunk_id=chunk,
                graph_id="gr_1",
                from_node_id="build",
                to_node_id="done",
                choice_name="success",
                epoch=1,
                runner_id="r1",
                recorded_at=_AT,
            )
        )
    )


def _ref(conn: sa.Connection, chunk: str, ref: str) -> None:
    conn.execute(
        sa.insert(s.chunk_work_refs).values(stamp(s.chunk_work_refs, chunk_id=chunk, source="default", ref=ref))
    )


def test_upgrade_recovers_only_outstanding_legacy_terminal_refs(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    with engine.begin() as conn:
        for chunk in ("legacy", "grouped", "deleted"):
            _terminal(conn, chunk)
        for ref in ("missing", "existing", "closed", "skipped", "failed"):
            _ref(conn, "legacy", ref)
        _ref(conn, "grouped", "grouped")
        _ref(conn, "deleted", "deleted")
        conn.execute(
            sa.insert(s.chunk_grouped).values(
                stamp(s.chunk_grouped, chunk_id="grouped", grouped_into="legacy", grouped_at=_AT)
            )
        )
        conn.execute(
            sa.insert(s.chunk_deleted).values(
                stamp(s.chunk_deleted, chunk_id="deleted", deleted_at=_AT, deleted_by="op")
            )
        )
        conn.execute(
            sa.insert(s.close_intents).values(
                stamp(
                    s.close_intents,
                    chunk_id="legacy",
                    source="default",
                    ref="existing",
                    enqueued_at=_AT,
                    retired_at=_AT,
                )
            )
        )
        for outcome in ("closed", "skipped", "failed"):
            conn.execute(
                sa.insert(s.work_item_closures).values(
                    stamp(
                        s.work_item_closures,
                        chunk_id="legacy",
                        source="default",
                        ref=outcome,
                        outcome=outcome,
                        reason=None,
                        recorded_at=_AT,
                    )
                )
            )

    runner.upgrade(_REVISION)
    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT ref, enqueued_at, retired_at FROM close_intents ORDER BY ref")).all()
    assert [r.ref for r in rows] == ["existing", "failed", "missing"]
    assert rows[0].retired_at is not None
    assert all(r.retired_at is None for r in rows[1:])
    assert not [v for v in hub_invariants(engine).run() if v.invariant == "hub:no-unenqueued-closable-ref"]

    # A new writer that omits the enqueue after the migration is still caught.
    with engine.begin() as conn:
        _terminal(conn, "new")
        _ref(conn, "new", "new")
    violations = [v.detail for v in hub_invariants(engine).run() if v.invariant == "hub:no-unenqueued-closable-ref"]
    assert len(violations) == 1 and "chunk new ref default#new" in violations[0]

    runner.downgrade(_PARENT)
    runner.upgrade(_REVISION)
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT count(*) FROM close_intents WHERE ref = 'existing'")).scalar_one() == 1


def test_upgrade_does_not_match_terminal_facts_across_tenants(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    with engine.begin() as conn:
        _terminal(conn, "legacy")
        _ref(conn, "legacy", "one")
        # A different tenant's matching closure must not satisfy this ref.
        conn.execute(
            sa.insert(s.work_item_closures).values(
                stamp(
                    s.work_item_closures,
                    tenant_id="other",
                    chunk_id="legacy",
                    source="default",
                    ref="one",
                    outcome="closed",
                    reason=None,
                    recorded_at=_AT,
                )
            )
        )
        # Nor may another tenant's transition make an unfinished chunk eligible.
        conn.execute(sa.insert(s.chunks).values(stamp(s.chunks, chunk_id="unfinished", graph_id="gr_1", minted_at=_AT)))
        _ref(conn, "unfinished", "two")
        conn.execute(
            sa.insert(s.transitions).values(
                stamp(
                    s.transitions,
                    tenant_id="other",
                    transition_id="tr_other",
                    chunk_id="unfinished",
                    graph_id="gr_1",
                    from_node_id="build",
                    to_node_id="done",
                    choice_name="success",
                    epoch=1,
                    runner_id="r1",
                    recorded_at=_AT,
                )
            )
        )
    runner.upgrade(_REVISION)
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT ref FROM close_intents")).scalars().all() == ["one"]


def test_upgrade_only_backfills_latest_terminal_movement(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    with engine.begin() as conn:
        for chunk in ("transition", "migration", "restart", "tie", "older", "other_tenant", "done_again"):
            _terminal(conn, chunk)
            _ref(conn, chunk, chunk)

        def transition(chunk: str, at: datetime, node: str, tenant: str | None = None) -> None:
            conn.execute(
                sa.insert(s.transitions).values(
                    stamp(
                        s.transitions,
                        **({"tenant_id": tenant} if tenant is not None else {}),
                        transition_id=f"later_{chunk}",
                        chunk_id=chunk,
                        graph_id="gr_1",
                        from_node_id="done",
                        to_node_id=node,
                        epoch=1,
                        runner_id="r1",
                        recorded_at=at,
                    )
                )
            )

        later = _AT + timedelta(seconds=1)
        transition("transition", later, "build")
        transition("tie", _AT, "build")
        transition("older", _AT - timedelta(seconds=1), "build")
        transition("other_tenant", later, "build", "other")
        transition("done_again", later, "done")
        conn.execute(
            sa.insert(s.chunk_migrations).values(
                stamp(
                    s.chunk_migrations,
                    migration_id="mg_1",
                    chunk_id="migration",
                    from_graph_id="gr_1",
                    to_graph_id="gr_2",
                    epoch=1,
                    recorded_at=later,
                )
            )
        )
        conn.execute(
            sa.insert(s.chunk_restarts).values(
                stamp(
                    s.chunk_restarts,
                    chunk_id="restart",
                    graph_id="gr_1",
                    to_node_id="build",
                    epoch=1,
                    restarted_by="op",
                    recorded_at=_AT,
                )
            )
        )

    runner.upgrade(_REVISION)
    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT ref, enqueued_at FROM close_intents ORDER BY ref")).all()
    assert [row.ref for row in rows] == ["done_again", "older", "other_tenant"]
    assert datetime.fromisoformat(rows[0].enqueued_at).replace(tzinfo=UTC) == later
