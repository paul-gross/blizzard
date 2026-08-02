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
from blizzard.hub.delivery.hub_node import ENV_MARKER_CALLBACK_URL, ENV_MARKER_TOKEN
from blizzard.hub.graphs.scripts.land_common import MarkerWriteError, marker_recorder
from tests.support import (
    FakeHubCommandRunner,
    HubHarness,
    build_hub,
    pointer_token,
    report_lease,
    seed_session,
    seed_user,
)

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


# -- the credential the executor actually injects, spent on the actual route ---------
#
# The two tests above build their recorder from a token the *test* mints off
# ``hub.services.marker_authority`` — the same instance the route's dependency reads —
# and from a callback URL the *test* formats. Neither reaches the executor, so two
# claims the shipped credential depends on are asserted nowhere:
#
#   * ``build_services`` hands the executor and ``HubServices`` the **same**
#     ``MarkerAuthority``. Give the executor its own instance and the whole
#     unit+component tier stays green while every real land against an ``oauth`` hub
#     401s — the exact production symptom issue #230 exists to close.
#   * ``HubNodeExecutor._marker_callback_url``'s ``?node_id=&epoch=`` — the triple the
#     route re-derives to verify against — names the same node id and epoch the
#     executor ``issue()``d the token under.
#
# The test below is the only place a token travels the whole shipped path: minted by
# the executor, injected into a real ``run:`` step's env, spent by the real
# ``marker_recorder`` on the real route, against a hub with authentication on. Its
# `run:` step stands in for the land script exactly where a land script sits — the
# subprocess boundary is the one thing it cannot reproduce in-process, and nothing
# about the credential lives there.

_HUB_NODE_GRAPH_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: merge
        fail:
          description: Incomplete.
          to: build
  merge:
    executor: hub
    run:
      - name: land
        command: land-the-repo
        produces: merged
    judgement:
      choices:
        success:
          description: Landed.
          to: done
        failure:
          description: Failed to land.
          to: build
"""


def _drive_to_the_hub_node(hub: HubHarness) -> str:
    """Ingest, promote, claim, and pass ``build`` so the completion's own apply runs the
    hub node executor synchronously — returning the chunk id.

    Only the three human-plane calls carry the admin session; the ``/api/fleet`` calls
    are the runner plane, and deliberately none of them sets a cookie on ``hub.client``
    itself. A client-wide cookie would ride the nested marker POST too, and the human
    ``require(CHUNK_CONTROL)`` fallback would then grant a write the marker token was
    supposed to be the only credential for — passing the test for the wrong reason.
    """
    admin = seed_user(hub, username="root", role=Role.SUPERUSER)
    cookie = _cookie(seed_session(hub, admin))
    assert (
        hub.client.post("/api/graphs", json={"definition_yaml": _HUB_NODE_GRAPH_YAML}, headers=cookie).status_code
        == 201
    )
    resp = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}, headers=cookie)
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote", headers=cookie).status_code == 202
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )
    assert claim.status_code == 201, claim.text
    build_node_id = claim.json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)
    apply = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={
            "choice": "pass",
            "epoch": 1,
            "runner_id": "r1",
            "from_node_id": build_node_id,
            "check_results": [],
            "artifacts": [],
        },
    )
    assert apply.json()["outcome"] == "hub_node_taken", apply.text
    return chunk_id


def test_the_token_the_executor_injects_authorizes_the_route_it_names(tmp_path: Path) -> None:
    """A hub node step spends the credential its own executor minted, and the marker
    lands — on a hub with authentication on."""
    runner = FakeHubCommandRunner()
    hub = build_hub(tmp_path, auth_mode="oauth", hub_command_runner=runner)
    write_errors: list[MarkerWriteError] = []

    def land_the_repo(_command: str) -> None:
        """Stand in for a land script at the exact point one runs: build the shipped
        ``marker_recorder`` out of nothing but the injected env, and spend it."""
        env = runner.calls[-1][2]
        record = marker_recorder(
            callback_url=env[ENV_MARKER_CALLBACK_URL],
            token=env.get(ENV_MARKER_TOKEN, ""),
            request=_hub_request(hub),
        )
        try:
            record("acme-widget", "sha:abc123")
        except MarkerWriteError as exc:
            # Captured rather than raised: an exception here escapes through the
            # executor into the completion request, and the test would fail on a 500
            # naming neither the status nor the refusal.
            write_errors.append(exc)

    runner.before_run = land_the_repo

    chunk_id = _drive_to_the_hub_node(hub)

    assert write_errors == []
    assert _MARKER_NAME in _recorded_marker_names(hub, chunk_id)
