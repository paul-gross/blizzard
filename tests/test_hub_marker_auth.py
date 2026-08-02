"""``require_marker_authority`` gating the mid-run marker callback (issue #230, phase 2).

Component tier: a real hub built with ``auth_mode="oauth"`` (:func:`build_hub`), hit
with a real HTTP client — proving the *dynamic* claim (an actual request with/without
a live marker token gets the actual status code the new gate predicts), the same way
``tests/test_route_permission_matrix.py`` proves the human-plane permission table.
Every refusal is proven both by status code AND by reading the marker artifact back
through ``services.chunks.load_artifacts`` — a route that 401s but records the write
anyway would pass a status-code-only check while still leaking the unauthenticated
write this issue exists to close.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from blizzard.auth_core import Role
from blizzard.hub.graphs.scripts.land_common import MarkerWriteError, marker_recorder
from tests.support import HubHarness, build_hub, pointer_token, seed_session, seed_user

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "1"}
_NODE_ID = "nd_merge"
_EPOCH = 1
_MARKER_NAME = "merged/acme-widget"


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"bz_session={token}"}


def _seed_chunk(hub: HubHarness) -> str:
    """A live, ingested chunk — minted through an authenticated admin session, since
    ingest itself needs ``CHUNK_INGEST`` under ``oauth``. The marker route's own gate
    is exercised afterward with no session at all (or a deliberately different one)."""
    admin = seed_user(hub, username="root", role=Role.SUPERUSER)
    admin_token = seed_session(hub, admin)
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}, headers=_cookie(admin_token))
    assert resp.status_code == 201, resp.text
    return resp.json()["chunk_id"]


def _marker_url(chunk_id: str, *, node_id: str = _NODE_ID, epoch: int = _EPOCH) -> str:
    return f"/api/chunks/{chunk_id}/hub-markers?node_id={node_id}&epoch={epoch}"


def _post_marker(hub: HubHarness, url: str, *, headers: dict[str, str] | None = None):
    return hub.client.post(url, json={"name": _MARKER_NAME, "content": "sha:abc123"}, headers=headers)


def _recorded_marker_names(hub: HubHarness, chunk_id: str) -> set[str]:
    return {a.name for a in hub.services.chunks.load_artifacts(chunk_id)}


def test_unauthenticated_post_is_refused_and_records_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    chunk_id = _seed_chunk(hub)

    resp = _post_marker(hub, _marker_url(chunk_id))

    assert resp.status_code == 401, resp.text
    assert _recorded_marker_names(hub, chunk_id) == set()


def test_valid_marker_token_is_granted_and_durably_recorded(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    chunk_id = _seed_chunk(hub)
    token = hub.services.marker_authority.issue(chunk_id, node_id=_NODE_ID, epoch=_EPOCH)

    resp = _post_marker(hub, _marker_url(chunk_id), headers={"X-Blizzard-Marker-Token": token})

    assert resp.status_code == 200, resp.text
    assert resp.json()["recorded"] is True
    assert _MARKER_NAME in _recorded_marker_names(hub, chunk_id)


def test_token_minted_for_a_different_node_is_refused_and_records_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    chunk_id = _seed_chunk(hub)
    token = hub.services.marker_authority.issue(chunk_id, node_id=_NODE_ID, epoch=_EPOCH)

    resp = _post_marker(hub, _marker_url(chunk_id, node_id="nd_other"), headers={"X-Blizzard-Marker-Token": token})

    assert resp.status_code == 401, resp.text
    assert _recorded_marker_names(hub, chunk_id) == set()


def test_token_minted_for_a_different_epoch_is_refused_and_records_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    chunk_id = _seed_chunk(hub)
    token = hub.services.marker_authority.issue(chunk_id, node_id=_NODE_ID, epoch=_EPOCH)

    resp = _post_marker(hub, _marker_url(chunk_id, epoch=_EPOCH + 1), headers={"X-Blizzard-Marker-Token": token})

    assert resp.status_code == 401, resp.text
    assert _recorded_marker_names(hub, chunk_id) == set()


def test_revoked_token_is_refused_and_records_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    chunk_id = _seed_chunk(hub)
    token = hub.services.marker_authority.issue(chunk_id, node_id=_NODE_ID, epoch=_EPOCH)
    hub.services.marker_authority.revoke(chunk_id, node_id=_NODE_ID, epoch=_EPOCH)

    resp = _post_marker(hub, _marker_url(chunk_id), headers={"X-Blizzard-Marker-Token": token})

    assert resp.status_code == 401, resp.text
    assert _recorded_marker_names(hub, chunk_id) == set()


def test_an_operators_own_chunk_control_session_still_works_with_no_marker_token(tmp_path: Path) -> None:
    """The marker-token gate is layered in **front of** the existing human gate, never
    in place of it — an operator's own ``CHUNK_CONTROL`` session, with no marker token
    at all, still passes exactly as it did before this issue."""
    hub = build_hub(tmp_path, auth_mode="oauth")
    chunk_id = _seed_chunk(hub)
    admin = seed_user(hub, username="root2", role=Role.SUPERUSER)
    admin_token = seed_session(hub, admin)

    resp = _post_marker(hub, _marker_url(chunk_id), headers=_cookie(admin_token))

    assert resp.status_code == 200, resp.text
    assert _MARKER_NAME in _recorded_marker_names(hub, chunk_id)


# -- the two sides of the credential, bound (issue #230) -----------------------------
#
# Every test above drives the route with a header literal the *test* writes, and every
# land-script test (``tests/test_land_common.py``, ``tests/test_land_scripts.py``,
# ``tests/test_land_ff.py``) asserts a header literal the *test* writes. Each side is
# proven against its own copy of the string, so renaming ``_MARKER_TOKEN_HEADER`` on
# one side alone — ``land_common``'s producer copy or ``api.marker_auth``'s consumer
# copy — leaves the whole gate green while every real land against an ``auth.mode !=
# "none"`` hub 401s. The two tests below are the only place the producer's real closure
# posts through the consumer's real route, so that rename fails here.
#
# `blizzard:e2e`'s delivery scenarios cannot cover it either: their hubs run at the
# `init` default (``auth.mode = "none"``), where ``require_marker_authority`` short-
# circuits before it ever reads the header.


def _hub_request(hub: HubHarness):
    """``land_common.forge_request``'s exact signature, funnelled through the wired hub
    app — the seam ``marker_recorder`` takes as ``request``, so the closure under test
    is the shipped one, sending the header name ``land_common`` actually sends."""

    def request(
        method: str,
        url: str,
        *,
        token: str | None,
        body: dict[str, Any] | None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        resp = hub.client.request(method, url, json=body, headers=headers)
        return resp.status_code, (resp.json() if resp.content else None)

    return request


def test_the_land_scripts_own_recorder_records_durably_against_an_oauth_hub(tmp_path: Path) -> None:
    """The issue's headline claim, end to end within one process: the real
    ``marker_recorder`` closure a land script builds, holding the real token the
    executor mints, writes a marker that is durably readable back — against a hub with
    authentication genuinely on."""
    hub = build_hub(tmp_path, auth_mode="oauth")
    chunk_id = _seed_chunk(hub)
    token = hub.services.marker_authority.issue(chunk_id, node_id=_NODE_ID, epoch=_EPOCH)
    record = marker_recorder(callback_url=_marker_url(chunk_id), token=token, request=_hub_request(hub))

    record("acme-widget", "sha:abc123")

    assert _MARKER_NAME in _recorded_marker_names(hub, chunk_id)


def test_the_land_scripts_own_recorder_fails_loudly_when_the_hub_refuses(tmp_path: Path) -> None:
    """The other half of non-contradictory: a recorder with no credential does not
    quietly report a landing against an authenticated hub — it raises out of the land
    stage (``MarkerWriteError``), and the refused write records nothing."""
    hub = build_hub(tmp_path, auth_mode="oauth")
    chunk_id = _seed_chunk(hub)
    record = marker_recorder(callback_url=_marker_url(chunk_id), token="", request=_hub_request(hub))

    with pytest.raises(MarkerWriteError):
        record("acme-widget", "sha:abc123")

    assert _recorded_marker_names(hub, chunk_id) == set()
