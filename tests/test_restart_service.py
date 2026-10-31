from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts
from blizzard.hub.domain.chunk.ports.decisions import IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites, ILockedChunkRead
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.graph.model import Graph, IReadGraphRepository
from blizzard.hub.domain.operations.restart import RestartService

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_1", work_refs=[], minted_at=_T0)


class _Untouchable:
    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"RestartService should not touch {name!r}")


class _GoneLockedChunkRead(_Untouchable):
    def facts(self, chunk_id: str) -> ChunkFacts | None:
        return None


class _FakeExclusiveWrites:
    @contextmanager
    def locked(self, chunk_ids: Sequence[str]) -> Iterator[ILockedChunkRead]:
        yield cast(ILockedChunkRead, _GoneLockedChunkRead())


def test_restart_refuses_a_chunk_gone_under_the_lock_and_writes_nothing() -> None:
    service = RestartService(
        movement=cast(IWriteChunkMovementRepository, _Untouchable()),
        decisions=cast(IWriteChunkDecisionsRepository, _Untouchable()),
        graphs=cast(IReadGraphRepository, _Untouchable()),
        clock=FixedClock(_T0),
        exclusive=cast(IChunkExclusiveWrites, _FakeExclusiveWrites()),
        closer_available=lambda source: source == "hub",
    )

    with pytest.raises(ChunkNotFound):
        service.restart(_CHUNK, cast(Graph, _Untouchable()), node_name=None, by="operator")
