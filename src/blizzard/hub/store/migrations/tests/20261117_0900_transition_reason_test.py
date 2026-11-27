"""The reason revision preserves historical transitions across both directions."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.hub.store import schema as s
from tests.support.migrations.store_migrations import schema_snapshot
from tests.support.scaffolding import migrate_to, seed_graph, stamp

pytestmark = pytest.mark.component

_PARENT = "20261116_0900_egress_order"
_REVISION = "20261117_0900_transition_reason"
_AT = datetime(2026, 11, 1, tzinfo=UTC)
_COLUMNS = (
    "transition_id, chunk_id, graph_id, from_node_id, to_node_id, choice_name, "
    "decision_id, epoch, runner_id, recorded_at, tenant_id"
)


def _rows(conn: sa.Connection) -> list[tuple[object, ...]]:
    return [tuple(row) for row in conn.execute(sa.text(f"SELECT {_COLUMNS} FROM transitions ORDER BY transition_id"))]


def test_populated_parent_upgrade_downgrade_upgrade(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = schema_snapshot(engine, exemptions={}, name_exemptions={})
    assert "reason" not in {col["name"] for col in sa.inspect(engine).get_columns("transitions")}
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_AT)
        conn.execute(sa.insert(s.chunks).values(stamp(s.chunks, chunk_id="ch_1", graph_id="gr_1", minted_at=_AT)))
        transitions = sa.Table("transitions", sa.MetaData(), autoload_with=conn)
        for transition_id, node, choice in (("tr_1", "review", "success"), ("tr_2", "done", "approve")):
            conn.execute(
                sa.insert(transitions).values(
                    stamp(
                        s.transitions,
                        transition_id=transition_id,
                        chunk_id="ch_1",
                        graph_id="gr_1",
                        from_node_id="build",
                        to_node_id=node,
                        choice_name=choice,
                        epoch=1,
                        runner_id="runner",
                        recorded_at=_AT,
                    )
                )
            )
        original = _rows(conn)

    runner.upgrade(_REVISION)
    columns = {col["name"]: col for col in sa.inspect(engine).get_columns("transitions")}
    assert columns["reason"]["nullable"] is True
    with engine.begin() as conn:
        assert _rows(conn) == original
        assert conn.execute(sa.text("SELECT reason FROM transitions ORDER BY transition_id")).scalars().all() == [
            None,
            None,
        ]
        conn.execute(sa.text("UPDATE transitions SET reason='approved' WHERE transition_id='tr_2'"))

    runner.downgrade(_PARENT)
    assert schema_snapshot(engine, exemptions={}, name_exemptions={}) == before
    with engine.connect() as conn:
        assert _rows(conn) == original

    runner.upgrade(_REVISION)
    with engine.connect() as conn:
        assert _rows(conn) == original
        assert conn.execute(sa.text("SELECT reason FROM transitions ORDER BY transition_id")).scalars().all() == [
            None,
            None,
        ]
