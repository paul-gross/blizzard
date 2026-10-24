"""The three deprecated marker and delivery routes admit only a person holding ``chunk:control``.

A real hub built with ``auth_mode="oauth"``, hit with a real HTTP client. A refusal is proven by its status and, on
the marker route, by reading the artifacts back — a route that refuses but records the write anyway would still be
a way in. No marker token is honoured: a request carrying only one has no session, and is refused like any other.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.auth_core import Role
from tests.support import HubHarness, build_hub, pointer_token, seed_session, seed_user

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "1"}
_NODE_ID = "nd_merge"
_EPOCH = 1
_MARKER_NAME = "merged/acme-widget"
_QUERY = f"node_id={_NODE_ID}&epoch={_EPOCH}"
_MARKER_TOKEN = {"X-Blizzard-Marker-Token": "x"}

#: Each deprecated route's path under a chunk, and the body it takes.
_ROUTES = {
    "hub-markers": {"name": _MARKER_NAME, "content": "sha:abc123"},
    "garden-delivery": {"delta": ["delta"], "proposals": ["docket"]},
    "review-findings-delivery": None,
}


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"bz_session={token}"}


def _seed_chunk(hub: HubHarness) -> str:
    """A live, ingested chunk — minted through an authenticated admin session, since ingest itself needs
    ``chunk:ingest`` under ``oauth``."""
    admin = seed_user(hub, username="root", role=Role.SUPERUSER)
    resp = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token(_POINTER)]}, headers=_cookie(seed_session(hub, admin))
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["chunk_id"]


def _post(hub: HubHarness, chunk_id: str, route: str, *, headers: dict[str, str] | None = None):
    return hub.client.post(f"/api/chunks/{chunk_id}/{route}?{_QUERY}", json=_ROUTES[route], headers=headers)


def _recorded_names(hub: HubHarness, chunk_id: str) -> set[str]:
    return {a.name for a in hub.services.chunks.artifacts.load_artifacts(chunk_id)}


@pytest.mark.parametrize("route", list(_ROUTES))
def test_a_request_with_only_a_marker_token_has_no_session_and_is_refused(tmp_path: Path, route: str) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    chunk_id = _seed_chunk(hub)
    before = _recorded_names(hub, chunk_id)

    resp = _post(hub, chunk_id, route, headers=_MARKER_TOKEN)

    assert resp.status_code == 401, resp.text
    assert _recorded_names(hub, chunk_id) == before


@pytest.mark.parametrize("route", list(_ROUTES))
def test_a_person_without_chunk_control_is_refused(tmp_path: Path, route: str) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    chunk_id = _seed_chunk(hub)
    guest = seed_user(hub, username="guest", role=Role.GUEST)

    resp = _post(hub, chunk_id, route, headers=_cookie(seed_session(hub, guest)))

    assert resp.status_code == 403, resp.text


def test_a_person_holding_chunk_control_records_a_marker(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    chunk_id = _seed_chunk(hub)
    admin = seed_user(hub, username="root2", role=Role.SUPERUSER)

    resp = _post(hub, chunk_id, "hub-markers", headers=_cookie(seed_session(hub, admin)))

    assert resp.status_code == 200, resp.text
    assert resp.json()["recorded"] is True
    assert _MARKER_NAME in _recorded_names(hub, chunk_id)


def test_the_three_routes_are_deprecated_in_the_openapi_document(tmp_path: Path) -> None:
    paths = build_hub(tmp_path).client.get("/openapi.json").json()["paths"]

    for route in _ROUTES:
        assert paths[f"/api/chunks/{{chunk_id}}/{route}"]["post"]["deprecated"] is True
