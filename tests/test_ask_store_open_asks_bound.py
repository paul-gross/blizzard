"""``AskStore.open_asks`` reads park rows only for the live asks it classifies (unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.runner.store.errors import RunnerStoreConnections
from blizzard.runner.store.internal.ask_store import AskStore
from blizzard.runner.store.schema import lease_closures
from tests.runner_fakes import _create_all, create_engine_from_url, runner_metadata, runner_store_errors

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_HISTORY = 25


class _RowCountingConnections(RunnerStoreConnections):
    """Real connections that count the rows every ``all`` read returns."""

    rows_read = 0

    def all(self, stmt):  # type: ignore[no-untyped-def]
        rows = super().all(stmt)
        self.rows_read += len(rows)
        return rows


def test_open_asks_does_not_read_the_park_rows_of_closed_leases(tmp_path) -> None:  # type: ignore[no-untyped-def]
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'runner.db'}")
    _create_all(runner_metadata, engine)
    counting = _RowCountingConnections(engine, runner_store_errors())
    asks = AskStore(counting)
    for i in range(_HISTORY):
        lease, question = f"lease_old_{i}", f"q_old_{i}"
        asks.record_ask(
            lease_id=lease, chunk_id="ch_old", question_id=question, question="?", options=[], asked_at=_NOW
        )
        asks.record_park(lease_id=lease, chunk_id="ch_old", question_id=question, parked_at=_NOW)
        asks.record_park_resume(lease_id=lease, question_id=question, resumed_at=_NOW)
        with counting.begin() as conn:
            conn.execute(
                lease_closures.insert().values(
                    lease_id=lease, chunk_id="ch_old", node_id="nd", reason="transitioned", closed_at=_NOW
                )
            )
    asks.record_ask(
        lease_id="lease_live", chunk_id="ch_live", question_id="q_live", question="?", options=[], asked_at=_NOW
    )
    asks.record_park(lease_id="lease_live", chunk_id="ch_live", question_id="q_live", parked_at=_NOW)

    open_asks = asks.open_asks()

    assert [a.question_id for a in open_asks] == ["q_live"]
    # one live ask + its one park fact; the closed history contributes nothing
    assert counting.rows_read == 2
