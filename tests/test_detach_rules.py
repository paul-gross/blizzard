"""Detach and retirement-release rules, pinned by value — no repository, no clock."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.execution.detach import (
    DETACHABLE,
    NotRouted,
    held_routes,
    holds,
    refuse_detach,
    releasable_by,
)
from blizzard.hub.domain.runners.route import Route

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _route(chunk_id: str = "chk_1", runner_id: str = "runner-a") -> Route:
    return Route(chunk_id=chunk_id, runner_id=runner_id, workspace_id="ws-a", environment_ids=["e1"], created_at=_T0)


def test_detach_is_legal_from_every_status_that_holds_a_claim() -> None:
    assert {
        ChunkStatus.NOT_READY,
        ChunkStatus.READY,
        ChunkStatus.RUNNING,
        ChunkStatus.DELIVERING,
        ChunkStatus.WAITING_ON_HUMAN,
        ChunkStatus.NEEDS_HUMAN,
        ChunkStatus.PAUSED,
    } == DETACHABLE


@pytest.mark.parametrize(
    "status",
    [
        ChunkStatus.RUNNING,
        ChunkStatus.DELIVERING,
        ChunkStatus.WAITING_ON_HUMAN,
        ChunkStatus.NEEDS_HUMAN,
        ChunkStatus.PAUSED,
        None,
    ],
)
def test_detach_releases_a_live_route_on_a_claim_holding_chunk(status: ChunkStatus | None) -> None:
    refuse_detach("chk_1", _route(), status)


@pytest.mark.parametrize("status", [ChunkStatus.RUNNING, ChunkStatus.DONE, None])
def test_detach_refuses_a_chunk_with_no_live_route(status: ChunkStatus | None) -> None:
    with pytest.raises(NotRouted, match="no live route"):
        refuse_detach("chk_1", None, status)


@pytest.mark.parametrize("status", [ChunkStatus.DONE, ChunkStatus.STOPPED])
def test_detach_refuses_a_route_left_on_a_terminal_chunk(status: ChunkStatus) -> None:
    with pytest.raises(NotRouted, match="holds no claim"):
        refuse_detach("chk_1", _route(), status)


@pytest.mark.parametrize(
    ("status", "held"),
    [(ChunkStatus.RUNNING, True), (ChunkStatus.DELIVERING, True), (None, True), (ChunkStatus.DONE, False)],
)
def test_a_terminal_chunks_route_is_no_holding(status: ChunkStatus | None, held: bool) -> None:
    assert holds(status) is held


def test_held_routes_drop_terminal_chunks_and_keep_chunks_with_no_status() -> None:
    running, done, stopped, unknown = _route("chk_r"), _route("chk_d"), _route("chk_s"), _route("chk_u")
    statuses = {"chk_r": ChunkStatus.RUNNING, "chk_d": ChunkStatus.DONE, "chk_s": ChunkStatus.STOPPED}

    assert held_routes([running, done, stopped, unknown], statuses) == [running, unknown]


def test_retirement_releases_only_its_own_route_on_a_claim_holding_chunk() -> None:
    assert releasable_by(_route(), ChunkStatus.RUNNING, runner_id="runner-a") is True
    assert releasable_by(_route(), None, runner_id="runner-a") is True


@pytest.mark.parametrize(
    ("route", "status"),
    [
        (None, ChunkStatus.RUNNING),
        (_route(runner_id="runner-b"), ChunkStatus.RUNNING),
        (_route(), ChunkStatus.DONE),
        (_route(), ChunkStatus.STOPPED),
    ],
)
def test_retirement_leaves_a_gone_foreign_or_terminal_route(route: Route | None, status: ChunkStatus) -> None:
    assert releasable_by(route, status, runner_id="runner-a") is False
