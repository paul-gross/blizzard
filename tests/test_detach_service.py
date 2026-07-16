"""DetachService (unit tier) — the operator-release write, facts only (D-088).

A fake stands in for the store — only ``route_of`` and ``record_route_released`` are
meaningfully implemented; every other seam is unreachable from
:meth:`DetachService.detach` and raises loudly if a regression starts calling it
(``bzh:domain-core`` — no store, no tokens).

``DetachService`` needs exactly those two members of :class:`IWriteChunkRepository`,
but every domain service in this package (``claim.py``, ``decisions.py``,
``ingest.py``, ``queue.py``, ``promote.py``, ``questions.py``, ...) takes the same
wide read+write Protocol — a service-specific narrower Protocol would be the lone
exception to that established shape (``bzh:repository-split`` names exactly the two
read/write variants, not a per-service slice), so this fake stays typed against the
full ``IWriteChunkRepository`` rather than inventing one. To keep the fake itself
small anyway, it implements only the two live methods and falls back to
``__getattr__`` for everything else — so growing the write repository with a new
method no longer breaks this file; pyright's structural check is bypassed with an
explicit, documented :func:`typing.cast` at the one call site that needs it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.detach import DetachService, NotRouted
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.work import Chunk, IWriteChunkRepository

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_1", pm_pointers=[], minted_at=_T0)


@dataclass
class _FakeChunkRepo:
    """Only ``route_of``/``record_route_released`` are live; anything else is a bug.

    Not typed against :class:`IWriteChunkRepository` directly — pyright cannot verify
    ``__getattr__``-backed structural conformance, so callers wrap an instance in
    :func:`_as_write_repo` instead."""

    route: Route | None
    released: list[tuple[str, datetime]] = field(default_factory=list)

    def route_of(self, chunk_id: str) -> Route | None:
        return self.route

    def record_route_released(self, chunk_id: str, *, at: datetime) -> None:
        self.released.append((chunk_id, at))

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"DetachService should not touch {name!r}")


def _as_write_repo(repo: _FakeChunkRepo) -> IWriteChunkRepository:
    """Assert the fake satisfies the Protocol DetachService depends on (see module docstring)."""
    return cast(IWriteChunkRepository, repo)


def _route(chunk_id: str = "chk_1") -> Route:
    return Route(chunk_id=chunk_id, runner_id="rn_1", workspace_id="ws_1", environment_ids=["env_1"], created_at=_T0)


def test_detach_releases_the_live_route_with_the_injected_clocks_now() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo(route=_route())
    service = DetachService(chunks=_as_write_repo(repo), clock=clock)

    service.detach(_CHUNK)

    assert repo.released == [("chk_1", _T0)]


def test_detach_raises_not_routed_and_writes_nothing_when_there_is_no_live_route() -> None:
    clock = FixedClock(instant=_T0)
    repo = _FakeChunkRepo(route=None)
    service = DetachService(chunks=_as_write_repo(repo), clock=clock)

    with pytest.raises(NotRouted):
        service.detach(_CHUNK)

    assert repo.released == []


def test_detach_uses_the_injected_clock_not_the_wall_clock() -> None:
    later = datetime(2026, 6, 1, tzinfo=UTC)
    clock = FixedClock(instant=later)
    repo = _FakeChunkRepo(route=_route())
    service = DetachService(chunks=_as_write_repo(repo), clock=clock)

    service.detach(_CHUNK)

    assert repo.released == [("chk_1", later)]
