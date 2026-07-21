"""``GET /api/leases/{id}/artifacts`` and ``.../artifacts/{name}`` (issue #127).

Exercised over a real store via TestClient, mirroring
``tests/test_runner_attachments_api.py`` (the token/lease auth half) and
``tests/test_pm_items_proxy.py`` (the hub is reached through a stubbed ``httpx.get``,
so the forward, its status pass-through, and the ``502`` on an unreachable hub are all
asserted against the real controller). The read is layered exactly like the attach
write: lease-scoped, token-authorized, then proxied to the hub's envelope route — the
worker holds no hub credential.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

import blizzard.runner.api.artifacts as artifacts_route
from blizzard.hub.domain.enrollment import hash_token
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.store.repository import NewLease
from tests.runner_fakes import make_store

_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)
_TOKEN = "the-lease-token"
_HUB_URL = "http://hub.local:8421"
_CHUNK = "ch_1"

# The hub's envelope payload the proxy forwards to — one artifact of each kind, so the
# route's kind-discriminated pass-through is proven for both.
_ENVELOPE: dict[str, object] = {
    "chunk_id": _CHUNK,
    "graph_id": "gr_1",
    "epoch": 3,
    "node": {
        "node_id": "nd_build",
        "node_name": "build",
        "executor": "runner",
        "session": "fresh",
        "judged_by": "worker",
    },
    "prompt": "build it",
    "judgement_prompt": None,
    "pm_pointers": [],
    "artifacts": [
        {
            "name": "plan",
            "kind": "asset",
            "node_name": "plan",
            "epoch": 1,
            "content": "the plan text",
        },
        {
            "name": "build-branch",
            "kind": "git_commit",
            "node_name": "build",
            "epoch": 2,
            "repo": "blizzard",
            "branch_name": "chunk/ch_1",
            "commit_hash": "abc123",
        },
    ],
}


class _FakeHubResponse:
    """A stand-in for the hub's ``httpx.Response`` on the proxy's outbound edge."""

    def __init__(self, status_code: int, payload: dict[str, object] | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> object:
        if self._payload is None:
            raise ValueError("no JSON body")
        return self._payload


def _app_with_store(tmp_path: Path, *, hub_url: str = _HUB_URL):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", hub_url=hub_url)
    return create_app(config, runner_store=store), store


def _seed_lease(store, **overrides: object) -> None:  # type: ignore[no-untyped-def]
    fields: dict[str, object] = {
        "lease_id": "lease_1",
        "chunk_id": _CHUNK,
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 1,
        "runner_id": "runner-local",
        "retries_max": 2,
        "created_at": _NOW,
    }
    fields.update(overrides)
    store.record_lease(NewLease(**fields))  # type: ignore[arg-type]
    store.record_lease_token(str(fields["lease_id"]), hash_token(_TOKEN), _NOW)


def _stub_hub(monkeypatch: pytest.MonkeyPatch, response: _FakeHubResponse, seen: list[str] | None = None) -> None:
    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        if seen is not None:
            seen.append(url)
        return response

    monkeypatch.setattr(artifacts_route.httpx, "get", fake_get)


# --------------------------------------------------------------------------- #
# Auth + wiring status map (no hub reached — resolved before the forward)
# --------------------------------------------------------------------------- #


@pytest.mark.component
def test_503_when_store_unwired(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=_HUB_URL)
    with TestClient(create_app(config)) as client:
        resp = client.get("/api/leases/lease_1/artifacts", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 503


@pytest.mark.component
def test_404_for_an_unknown_lease(tmp_path: Path) -> None:
    app, _store = _app_with_store(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_ghost/artifacts", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404


@pytest.mark.component
def test_403_for_a_missing_token(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts")
    assert resp.status_code == 403


@pytest.mark.component
def test_403_for_a_wrong_token(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts", headers={"X-Blizzard-Lease-Token": "nope"})
    assert resp.status_code == 403


@pytest.mark.component
def test_a_closed_lease_is_404_not_403(tmp_path: Path) -> None:
    """A closed lease resolves to nothing active — 404 (unknown/closed) before the token
    check, exactly like attach."""
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id=_CHUNK, node_id="nd_build", reason="transitioned", closed_at=_NOW)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404


@pytest.mark.component
def test_503_when_hub_unwired_even_for_an_authorized_lease(tmp_path: Path) -> None:
    """An empty ``hub_url`` (store-free / unwired hub) is 503 — but only after auth, so an
    unauthorized caller never learns the hub-wiring state."""
    app, store = _app_with_store(tmp_path, hub_url="")
    _seed_lease(store)
    with TestClient(app) as client:
        authed = client.get("/api/leases/lease_1/artifacts", headers={"X-Blizzard-Lease-Token": _TOKEN})
        unauthed = client.get("/api/leases/lease_1/artifacts", headers={"X-Blizzard-Lease-Token": "nope"})
    assert authed.status_code == 503
    assert unauthed.status_code == 403


# --------------------------------------------------------------------------- #
# The forward + kind-discriminated read (hub stubbed)
# --------------------------------------------------------------------------- #


@pytest.mark.component
def test_list_forwards_to_the_hub_envelope_and_returns_both_kinds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    seen: list[str] = []
    _stub_hub(monkeypatch, _FakeHubResponse(200, _ENVELOPE), seen)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    # It forwarded to the hub's runner-authenticated envelope route for the resolved chunk.
    assert seen == [f"{_HUB_URL}/api/fleet/chunks/{_CHUNK}/envelope"]
    body = resp.json()
    assert [a["name"] for a in body] == ["plan", "build-branch"]
    asset = next(a for a in body if a["kind"] == "asset")
    assert asset["content"] == "the plan text" and asset["repo"] is None
    git = next(a for a in body if a["kind"] == "git_commit")
    assert git["branch_name"] == "chunk/ch_1" and git["commit_hash"] == "abc123" and git["content"] is None


@pytest.mark.component
def test_list_forwards_the_runner_bearer_when_a_token_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The forward rides the runner principal's bearer (issue #86b) — the worker's own
    lease token never leaves the runner."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(
        root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", hub_url=_HUB_URL, hub_token="hub-tok"
    )
    _seed_lease(store)
    seen_headers: list[dict[str, str]] = []

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        seen_headers.append(dict(headers))
        return _FakeHubResponse(200, _ENVELOPE)

    monkeypatch.setattr(artifacts_route.httpx, "get", fake_get)
    with TestClient(create_app(config, runner_store=store)) as client:
        resp = client.get("/api/leases/lease_1/artifacts", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert seen_headers == [{"Authorization": "Bearer hub-tok"}]


@pytest.mark.component
def test_get_returns_one_artifact_by_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(monkeypatch, _FakeHubResponse(200, _ENVELOPE))
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts/plan", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "name": "plan",
        "kind": "asset",
        "node_name": "plan",
        "epoch": 1,
        "content": "the plan text",
        "repo": None,
        "branch_name": None,
        "commit_hash": None,
    }


@pytest.mark.component
def test_get_404_for_an_unknown_artifact_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(monkeypatch, _FakeHubResponse(200, _ENVELOPE))
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts/ghost", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]


@pytest.mark.component
def test_passes_through_the_hub_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A hub 404 (unknown chunk) surfaces as a 404 with the hub's detail."""
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(monkeypatch, _FakeHubResponse(404, {"detail": "unknown chunk ch_1"}))
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "unknown chunk ch_1"


@pytest.mark.component
def test_502_when_the_hub_is_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(artifacts_route.httpx, "get", fake_get)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 502
    assert "unreachable" in resp.json()["detail"]
