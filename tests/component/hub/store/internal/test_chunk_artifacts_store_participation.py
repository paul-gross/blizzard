"""``record_hub_artifact`` joins a caller's unit that locks the chunk, and refuses any other token —
real adapters, real migrations."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import func, select

from blizzard.hub.domain.chunk.model import CloseEnqueueDecision
from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.kernel.transactions import LockedTransaction, TransactionMisuse
from blizzard.hub.store import schema as s
from blizzard.hub.store.transactions import HubTransactions
from tests.support.scaffolding import HubHarness, build_hub, hub_scoped, hub_store_connections, ingest

_NAME = "progress/one"


def _record(hub: HubHarness, chunk_id: str, transaction: LockedTransaction) -> bool:
    return (
        cast(IWriteChunkArtifactsRepository, hub.services.chunks.artifacts)
        .record_hub_artifact(
            chunk_id,
            node_id="nd_deliver",
            node_name="deliver",
            epoch=1,
            name=_NAME,
            content="x",
            at=hub.clock.now(),
            admission=EpochAdmission.AT_OR_ABOVE,
            terminal=False,
            close_decision=CloseEnqueueDecision(closable=frozenset(), skipped=frozenset()),
            transaction=transaction,
        )
        .wrote
    )


def _count(hub: HubHarness, chunk_id: str) -> int:
    with hub.engine.connect() as conn:
        return conn.execute(
            hub_scoped(
                select(func.count())
                .select_from(s.artifacts)
                .where((s.artifacts.c.chunk_id == chunk_id) & (s.artifacts.c.name == _NAME))
            )
        ).scalar_one()


def test_joined_write_lands_on_the_unit_and_rolls_back_with_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    transactions = HubTransactions(hub_store_connections(hub.engine))

    with pytest.raises(ValueError), transactions.begin() as unit:
        assert _record(hub, chunk_id, unit.lock_chunks([chunk_id])) is True
        raise ValueError("abort")

    assert _count(hub, chunk_id) == 0
    with transactions.begin() as unit:
        assert _record(hub, chunk_id, unit.lock_chunks([chunk_id])) is True
    assert _count(hub, chunk_id) == 1


def test_an_unlocked_token_is_refused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)

    with HubTransactions(hub_store_connections(hub.engine)).begin() as unit, pytest.raises(TransactionMisuse):
        _record(hub, chunk_id, cast(LockedTransaction, unit))

    assert _count(hub, chunk_id) == 0


def test_a_joined_token_that_does_not_cover_the_chunk_is_refused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    other_id = ingest(hub, [{"source": "default", "ref": "2"}], promote=True)
    transactions = HubTransactions(hub_store_connections(hub.engine))

    with transactions.begin() as unit:
        locked = unit.lock_chunks([other_id])
        with pytest.raises(TransactionMisuse):
            _record(hub, chunk_id, locked)

    assert _count(hub, chunk_id) == 0


def test_a_joined_token_from_an_ended_unit_is_refused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    with HubTransactions(hub_store_connections(hub.engine)).begin() as unit:
        locked = unit.lock_chunks([chunk_id])

    with pytest.raises(TransactionMisuse):
        _record(hub, chunk_id, locked)

    assert _count(hub, chunk_id) == 0
