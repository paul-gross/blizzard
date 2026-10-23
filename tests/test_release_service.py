"""ReleaseService (unit tier) — the holder's own release, decided under the chunk's lock and
written as one ``route.released`` fact carrying its cause and reason.

A fake stands in for the store — only ``route_of``/``facts`` on the locked handle and
``record_route_released_locked`` are live; every other seam raises loudly if called."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.tokens import TokenHash
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkFacts,
    EpochOwnerFact,
    RouteCreatedFact,
    RouteReleaseCause,
    RouteTokenMintedFact,
)
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites, ILockedChunkRead
from blizzard.hub.domain.chunk.ports.route import IWriteChunkRouteRepository
from blizzard.hub.domain.execution.auth.route import ROUTE_TOKEN_ENFORCE, ROUTE_TOKEN_WARN
from blizzard.hub.domain.execution.release import ReleaseRefusalKind, ReleaseRefused, ReleaseService
from blizzard.hub.domain.runners.route import Route

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CHUNK = Chunk(chunk_id="chk_1", graph_id="gr_1", work_refs=[], minted_at=_T0)
_TOKEN = "route-token"
_ROUTE = Route(chunk_id="chk_1", runner_id="rn_1", workspace_id="ws_1", environment_ids=["env_1"], created_at=_T0)
_FACTS = ChunkFacts(
    minted=True,
    promoted=True,
    routes_created=[RouteCreatedFact(created_at=_T0, seq=1)],
    route_tokens_minted=[RouteTokenMintedFact(token_hash=TokenHash(_TOKEN).hex, minted_at=_T0, seq=2)],
    epoch_owners=[EpochOwnerFact(epoch=1, runner_id="rn_1")],
)


@dataclass
class _FakeLockedChunkRead:
    route: Route | None
    loaded: ChunkFacts

    def route_of(self, chunk_id: str) -> Route | None:
        return self.route

    def facts(self, chunk_id: str) -> ChunkFacts:
        return self.loaded

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"ReleaseService should not touch handle.{name!r}")


@dataclass
class _FakeExclusiveWrites:
    route: Route | None
    facts: ChunkFacts
    locked_ids: list[list[str]] = field(default_factory=list)

    @contextmanager
    def locked(self, chunk_ids: Sequence[str]) -> Iterator[ILockedChunkRead]:
        self.locked_ids.append(list(chunk_ids))
        yield cast(ILockedChunkRead, _FakeLockedChunkRead(self.route, self.facts))


@dataclass
class _FakeRouteRepo:
    released: list[tuple[str, datetime, RouteReleaseCause | None, str | None]] = field(default_factory=list)

    def record_route_released_locked(
        self,
        handle: ILockedChunkRead,
        chunk_id: str,
        *,
        at: datetime,
        cause: RouteReleaseCause | None = None,
        reason: str | None = None,
    ) -> int:
        self.released.append((chunk_id, at, cause, reason))
        return 7

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"ReleaseService should not touch {name!r}")


def _service(
    route: Route | None = _ROUTE, facts: ChunkFacts = _FACTS
) -> tuple[ReleaseService, _FakeRouteRepo, _FakeExclusiveWrites]:
    repo = _FakeRouteRepo()
    exclusive = _FakeExclusiveWrites(route, facts)
    service = ReleaseService(
        route=cast(IWriteChunkRouteRepository, repo),
        exclusive=cast(IChunkExclusiveWrites, exclusive),
        clock=FixedClock(instant=_T0),
    )
    return service, repo, exclusive


def _release(service: ReleaseService, **overrides: Any) -> int:
    kwargs: dict[str, Any] = {
        "runner_id": "rn_1",
        "epoch": 1,
        "route_token": _TOKEN,
        "reason": "throttled",
        "route_token_mode": ROUTE_TOKEN_ENFORCE,
    }
    kwargs.update(overrides)
    return service.release(_CHUNK, **kwargs)


def test_an_admitted_release_writes_one_release_fact_with_its_reason_under_the_lock() -> None:
    service, repo, exclusive = _service()

    released_id = _release(service)

    assert released_id == 7
    assert exclusive.locked_ids == [["chk_1"]]
    assert repo.released == [("chk_1", _T0, RouteReleaseCause.RELEASE, "throttled")]


def test_the_reason_is_recorded_as_given() -> None:
    service, repo, _ = _service()

    _release(service, reason="anything-a-new-caller-says")

    assert repo.released[0][3] == "anything-a-new-caller-says"


@pytest.mark.parametrize(
    ("overrides", "kind"),
    [
        ({"runner_id": "rn_2", "route_token_mode": ROUTE_TOKEN_WARN}, ReleaseRefusalKind.NOT_HOLDER),
        ({"epoch": 0}, ReleaseRefusalKind.STALE),
    ],
    ids=["not-holder", "stale"],
)
def test_a_refused_release_writes_nothing(overrides: dict[str, Any], kind: ReleaseRefusalKind) -> None:
    service, repo, _ = _service()

    with pytest.raises(ReleaseRefused) as refused:
        _release(service, **overrides)

    assert refused.value.kind is kind
    assert repo.released == []


def test_a_mismatched_route_token_is_refused_under_enforce() -> None:
    service, repo, _ = _service()

    with pytest.raises(ReleaseRefused) as refused:
        _release(service, route_token="not-the-token")

    assert refused.value.kind is ReleaseRefusalKind.NOT_HOLDER
    assert repo.released == []


def test_a_mismatched_route_token_only_logs_under_warn() -> None:
    service, repo, _ = _service()

    _release(service, route_token="not-the-token", route_token_mode=ROUTE_TOKEN_WARN)

    assert [cause for _, _, cause, _ in repo.released] == [RouteReleaseCause.RELEASE]
