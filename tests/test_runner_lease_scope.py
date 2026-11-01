"""``lease_for_presented_token`` at the dependency itself: a missing token, an unknown hash and a hash
naming a closed lease are one ``403`` with one detail, and only an authorized lease annotates the worker caller."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from starlette.datastructures import State

from blizzard.foundation.tokens import TokenHash
from blizzard.runner.api import lease_scope
from blizzard.runner.api.lease_scope import lease_for_presented_token
from blizzard.runner.leases.model import Lease

pytestmark = pytest.mark.unit

_REFUSED_DETAIL = "presented token does not authorize a lease"
_LEASE = Lease(
    lease_id="lease_1",
    chunk_id="ch_1",
    graph_id="gr_1",
    node_id="nd_build",
    node_name="build",
    epoch=1,
    retries_max=2,
    created_at=datetime(2026, 7, 1, tzinfo=UTC),
)


def _request(*, token: str | None, active: Lease | None) -> Request:
    """A request carrying ``token`` against stores that know ``secret`` as ``lease_1``'s token,
    with ``active`` as that lease's active record and no open takeover."""

    def active_lease(lease_id: str) -> Lease | None:
        return active if lease_id == "lease_1" else None

    def lease_for_open_takeover(_lease_id: str) -> None:
        return None

    stores = SimpleNamespace(
        tokens=SimpleNamespace(lease_for_token_hash={TokenHash("secret").hex: "lease_1"}.get),
        lease_record=SimpleNamespace(active_lease=active_lease),
        takeover=SimpleNamespace(lease_for_open_takeover=lease_for_open_takeover),
    )
    state = State()
    state.runner_read_stores = stores
    headers = [] if token is None else [(b"x-blizzard-lease-token", token.encode())]
    return Request({"type": "http", "app": SimpleNamespace(state=state), "headers": headers, "state": {}})


@pytest.fixture
def callers(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    annotated: list[str] = []
    monkeypatch.setattr(lease_scope, "annotate_caller", annotated.append)
    return annotated


@pytest.mark.parametrize(
    ("token", "active"),
    [(None, _LEASE), ("unknown", _LEASE), ("secret", None)],
    ids=["missing", "unknown", "closed"],
)
def test_a_token_authorizing_no_lease_is_one_403_naming_no_lease(
    token: str | None, active: Lease | None, callers: list[str]
) -> None:
    with pytest.raises(HTTPException) as refused:
        lease_for_presented_token(_request(token=token, active=active))
    assert (refused.value.status_code, refused.value.detail) == (403, _REFUSED_DETAIL)
    assert callers == []


def test_the_active_leases_token_resolves_it_and_annotates_the_worker_caller(callers: list[str]) -> None:
    assert lease_for_presented_token(_request(token="secret", active=_LEASE)) == _LEASE
    assert callers == ["worker"]
