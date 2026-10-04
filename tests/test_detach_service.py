"""DetachService (unit tier) — the operator-release write, facts only.

A fake stands in for the store — only ``route_of``/``record_route_released_locked`` are
meaningfully implemented; every other seam raises loudly if called (``bzh:domain-core``).
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites, ILockedChunkRead
from blizzard.hub.domain.chunk.ports.route import IWriteChunkRouteRepository
from blizzard.hub.domain.execution.detach import DetachService, NotRouted
from blizzard.hub.domain.runners.route import Route

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_1", work_refs=[], minted_at=_T0)


@dataclass
class _FakeLockedChunkRead:
    """Only ``route_of`` is live — see module docstring."""

    route: Route | None

    def route_of(self, chunk_id: str) -> Route | None:
        return self.route

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"DetachService should not touch handle.{name!r}")


@dataclass
class _FakeExclusiveWrites:
    """``locked`` yields a :class:`_FakeLockedChunkRead` over the same route the fake
    previously exposed as a direct repository read."""

    route: Route | None

    @contextmanager
    def locked(self, chunk_ids: Sequence[str]) -> Iterator[ILockedChunkRead]:
        yield cast(ILockedChunkRead, _FakeLockedChunkRead(self.route))


@dataclass
class _FakeChunkRepo:
    """Only ``record_route_released_locked`` is live; anything else is a bug.

    Callers wrap an instance in :func:`_as_route` for pyright's structural check."""

    released: list[tuple[str, datetime]] = field(default_factory=list)

    def record_route_released_locked(self, handle: ILockedChunkRead, chunk_id: str, *, at: datetime) -> None:
        self.released.append((chunk_id, at))

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"DetachService should not touch {name!r}")


def _as_route(repo: _FakeChunkRepo) -> IWriteChunkRouteRepository:
    """Assert the fake satisfies the Protocol DetachService depends on (see module docstring)."""
    return cast(IWriteChunkRouteRepository, repo)


def _route(chunk_id: str = "chk_1") -> Route:
    return Route(chunk_id=chunk_id, runner_id="rn_1", workspace_id="ws_1", environment_ids=["env_1"], created_at=_T0)


def _service(route: Route | None, *, clock: FixedClock | None = None) -> tuple[DetachService, _FakeChunkRepo]:
    repo = _FakeChunkRepo()
    exclusive = cast(IChunkExclusiveWrites, _FakeExclusiveWrites(route))
    service = DetachService(route=_as_route(repo), exclusive=exclusive, clock=clock or FixedClock(instant=_T0))
    return service, repo


def test_detach_releases_the_live_route_with_the_injected_clocks_now() -> None:
    service, repo = _service(_route())

    service.detach(_CHUNK)

    assert repo.released == [("chk_1", _T0)]


def test_detach_raises_not_routed_and_writes_nothing_when_there_is_no_live_route() -> None:
    service, repo = _service(None)

    with pytest.raises(NotRouted):
        service.detach(_CHUNK)

    assert repo.released == []


def test_detach_uses_the_injected_clock_not_the_wall_clock() -> None:
    later = datetime(2026, 6, 1, tzinfo=UTC)
    service, repo = _service(_route(), clock=FixedClock(instant=later))

    service.detach(_CHUNK)

    assert repo.released == [("chk_1", later)]
