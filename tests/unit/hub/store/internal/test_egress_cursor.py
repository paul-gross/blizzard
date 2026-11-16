"""Cursor authority and ordering at the store adapter boundary."""

from contextlib import nullcontext
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from blizzard.hub.domain.observability.egress.repository import EgressCheckpoint, UsagePosition
from blizzard.hub.store.internal.egress_store import EgressStore, _current


def test_append_requires_exact_predecessor_and_advances_order() -> None:
    conn = MagicMock()
    store_connections = MagicMock()
    store_connections.write.return_value = nullcontext(conn)
    store = EgressStore(store_connections, hub_events=MagicMock(), usage=MagicMock())
    now = datetime(2026, 1, 1, tzinfo=UTC)
    record = EgressCheckpoint("invocations", None, UsagePosition(now), 0, (), now)
    previous = SimpleNamespace(id=7, append_order=3)
    with (
        patch("blizzard.hub.store.internal.egress_store.lock_hub"),
        patch("blizzard.hub.store.internal.egress_store._current", return_value=previous),
        patch("blizzard.hub.store.internal.egress_store._insert") as insert,
    ):
        assert not store.append_cursor(record, after=None)
        assert not store.append_cursor(record, after=8)
        insert.assert_not_called()
        assert store.append_cursor(record, after=7)
        insert.assert_called_once_with(conn, record, 4)


def test_current_selects_append_order_before_recorded_time() -> None:
    conn = MagicMock()
    _current(conn, "invocations")
    statement = conn.execute.call_args.args[0]
    order = list(statement._order_by_clauses)
    assert [str(clause) for clause in order] == [
        "egress_cursor.append_order DESC",
        "egress_cursor.recorded_at DESC",
        "egress_cursor.id DESC",
    ]
