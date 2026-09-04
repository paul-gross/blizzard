"""``GET /api/leases/{id}/artifacts`` and ``.../artifacts/{name}``.

Exercised over a real store via TestClient, hub reached through a stubbed ``httpx.Client``
so the forward, status pass-through, and 502-on-unreachable are asserted for real. Layered
like the attach write — lease-scoped, token-authorized, then proxied; a ``--scope graph``
read resolves from the store's own pinned mirror and never reaches the stub."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.tokens import TokenHash
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.artifacts import GraphArtifactRecord
from blizzard.runner.domain.leases import NewLease
from tests.runner_fakes import make_store, make_stores

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
    "work_refs": [],
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
    """A status/payload pair for ``_stub_hub`` to answer with — converted to a real
    ``httpx.Response`` at dispatch time, never duck-typed for ``hub_proxy`` itself."""

    def __init__(self, status_code: int, payload: object | None = None, text: str = "") -> None:
        self.status_code = status_code
        self.payload = payload
        self.text = text

    def to_httpx_response(self) -> httpx.Response:
        if self.payload is None:
            return httpx.Response(self.status_code, text=self.text)
        return httpx.Response(self.status_code, json=self.payload)


class _HubRouter:
    """A late-bound handler behind the proxy's ``httpx.Client`` — ``_app_with_store`` builds
    the client (and the app wired to it) before a test knows how the hub should answer, so
    ``_stub_hub`` arms this after the fact instead of monkeypatching a module-level function."""

    def __init__(self) -> None:
        self.handler: Callable[[httpx.Request], httpx.Response] = lambda request: httpx.Response(
            500, json={"detail": f"hub not stubbed for {request.url}"}
        )

    def __call__(self, request: httpx.Request) -> httpx.Response:
        return self.handler(request)


def _app_with_store(tmp_path: Path, *, hub_url: str = _HUB_URL):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", hub_url=hub_url)
    router = _HubRouter()
    app = create_app(
        config, runner_stores=make_stores(store), hub_proxy_client=httpx.Client(transport=httpx.MockTransport(router))
    )
    app.state.hub_router = router
    return app, store


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
    store.record_lease_token(str(fields["lease_id"]), TokenHash(_TOKEN).hex, _NOW)


_SYSTEM_ARTIFACTS_PATH = f"{_HUB_URL}/api/fleet/system-artifacts"


def _stub_hub(
    app: FastAPI,
    response: _FakeHubResponse,
    seen: list[str] | None = None,
    *,
    system_list: _FakeHubResponse | None = None,
    system_get: _FakeHubResponse | None = None,
) -> None:
    """Every hub call answers with ``response``, except one under ``/system-artifacts``, which
    defaults to "nothing published" (an empty list / a 404) — so a test not exercising
    system scope needs no shape for it; ``system_list``/``system_get`` override either."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if seen is not None:
            seen.append(url)
        if url == _SYSTEM_ARTIFACTS_PATH:
            picked = system_list if system_list is not None else _FakeHubResponse(200, [])
        elif url.startswith(f"{_SYSTEM_ARTIFACTS_PATH}/"):
            picked = system_get if system_get is not None else _FakeHubResponse(404, {"detail": "no system artifact"})
        else:
            picked = response
        return picked.to_httpx_response()

    app.state.hub_router.handler = handler


# Auth + wiring status map (no hub reached — resolved before the forward)


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
def test_an_open_takeover_authorizes_a_closed_reference_lease(tmp_path: Path) -> None:
    """The worker-authorization resolver's other half (issue #291): once an open
    takeover names the (now closed) reference lease, its re-minted token reaches
    this route the same as an ordinary active lease would."""
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id=_CHUNK, node_id="nd_build", reason="escalated", closed_at=_NOW)
    takeover_token = "the-takeover-token"
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id=_CHUNK,
        lease_id="lease_1",
        session_id="sess-a",
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )
    store.record_lease_token("lease_1", TokenHash(takeover_token).hex, _NOW)
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE))

    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts", headers={"X-Blizzard-Lease-Token": takeover_token})
    assert resp.status_code == 200, resp.text


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


# The forward + kind-discriminated read (hub stubbed)


@pytest.mark.component
def test_list_forwards_to_the_hub_envelope_and_returns_both_kinds(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    seen: list[str] = []
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE), seen)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    # It forwarded to the hub's runner-authenticated envelope route for the resolved chunk,
    # then to the system-artifact set (empty here, since none is stubbed).
    assert seen == [f"{_HUB_URL}/api/fleet/chunks/{_CHUNK}/envelope", _SYSTEM_ARTIFACTS_PATH]
    body = resp.json()
    assert [a["name"] for a in body] == ["plan", "build-branch"]
    asset = next(a for a in body if a["kind"] == "asset")
    assert asset["content"] == "the plan text" and asset["repo"] is None
    git = next(a for a in body if a["kind"] == "git_commit")
    assert git["branch_name"] == "chunk/ch_1" and git["commit_hash"] == "abc123" and git["content"] is None


@pytest.mark.component
def test_list_forwards_the_runner_bearer_when_a_token_is_configured(tmp_path: Path) -> None:
    """The forward rides the runner principal's bearer (issue #86b) — the worker's own
    lease token never leaves the runner."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(
        root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", hub_url=_HUB_URL, hub_token="hub-tok"
    )
    _seed_lease(store)
    seen_headers: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(request.headers["authorization"])
        if str(request.url) == _SYSTEM_ARTIFACTS_PATH:
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=_ENVELOPE)

    hub_proxy_client = httpx.Client(transport=httpx.MockTransport(handler))
    with TestClient(create_app(config, runner_stores=make_stores(store), hub_proxy_client=hub_proxy_client)) as client:
        resp = client.get("/api/leases/lease_1/artifacts", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    # Two forwards — envelope, then the system-artifact set — both riding the same bearer.
    assert seen_headers == ["Bearer hub-tok", "Bearer hub-tok"]


@pytest.mark.component
def test_get_returns_one_artifact_by_name(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE))
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts/plan", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "scope": "node",
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
def test_get_resolves_a_slash_containing_name(tmp_path: Path) -> None:
    """A ``merged/<repo>`` delivery marker (issue #233) — the route's ``{name:path}``
    converter must capture the slash rather than treating it as a path boundary."""
    envelope = {
        **_ENVELOPE,
        "artifacts": [
            {"name": "merged/blizzard", "kind": "asset", "node_name": "deliver", "epoch": 1, "content": "merged"},
        ],
    }
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(app, _FakeHubResponse(200, envelope))
    with TestClient(app) as client:
        resp = client.get(
            f"/api/leases/lease_1/artifacts/{quote('merged/blizzard', safe='/')}",
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "merged/blizzard"


@pytest.mark.component
def test_get_404_for_an_unknown_artifact_name(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE))
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts/ghost", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]


# The envelope shape a chunk with several node-steps producing the same `produces:`
# name (issue #169) — two `retrospective` entries, one per producing node.
_ENVELOPE_WITH_DUPLICATE_NAME: dict[str, object] = {
    **_ENVELOPE,
    "artifacts": [
        {"name": "retrospective", "kind": "asset", "node_name": "plan", "epoch": 1, "content": "plan's take"},
        {"name": "retrospective", "kind": "asset", "node_name": "build", "epoch": 2, "content": "build's take"},
    ],
}


@pytest.mark.component
def test_get_409_when_a_bare_name_resolves_to_more_than_one_node(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE_WITH_DUPLICATE_NAME))
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts/retrospective", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "retrospective" in detail
    assert "build" in detail and "plan" in detail


@pytest.mark.component
def test_get_node_query_param_disambiguates(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE_WITH_DUPLICATE_NAME))
    with TestClient(app) as client:
        resp = client.get(
            "/api/leases/lease_1/artifacts/retrospective",
            params={"node": "build"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["node_name"] == "build"
    assert resp.json()["content"] == "build's take"


@pytest.mark.component
def test_get_node_query_param_404_when_that_node_never_produced_the_name(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE_WITH_DUPLICATE_NAME))
    with TestClient(app) as client:
        resp = client.get(
            "/api/leases/lease_1/artifacts/retrospective",
            params={"node": "review"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 404
    assert "review" in resp.json()["detail"]


@pytest.mark.component
def test_passes_through_the_hub_status(tmp_path: Path) -> None:
    """A hub 404 (unknown chunk) surfaces as a 404 with the hub's detail."""
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(app, _FakeHubResponse(404, {"detail": "unknown chunk ch_1"}))
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "unknown chunk ch_1"


@pytest.mark.component
def test_502_when_the_hub_is_unreachable(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    app.state.hub_router.handler = handler
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 502
    assert "unreachable" in resp.json()["detail"]


# Graph scope — the runner's own store-read mirror, never the hub


def _seed_graph_artifacts(store, graph_id: str = "gr_1") -> None:  # type: ignore[no-untyped-def]
    store.record_graph_artifacts(
        graph_id=graph_id,
        artifacts=[GraphArtifactRecord(name="docket", ordinal=0, kind=ArtifactKind.ASSET, content="the docket text")],
        recorded_at=_NOW,
    )


@pytest.mark.component
def test_list_with_no_scope_combines_node_and_graph_rows_each_carrying_its_scope(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _seed_graph_artifacts(store)
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE))
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    scopes = {a["name"]: a["scope"] for a in body}
    assert scopes["plan"] == "node"
    assert scopes["docket"] == "graph"
    docket = next(a for a in body if a["name"] == "docket")
    assert docket["kind"] == "asset"
    assert docket["node_name"] is None
    assert docket["epoch"] is None
    assert docket["content"] == "the docket text"


@pytest.mark.component
def test_list_scope_node_excludes_graph_rows(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _seed_graph_artifacts(store)
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE))
    with TestClient(app) as client:
        resp = client.get(
            "/api/leases/lease_1/artifacts", params={"scope": "node"}, headers={"X-Blizzard-Lease-Token": _TOKEN}
        )
    assert resp.status_code == 200, resp.text
    assert {a["name"] for a in resp.json()} == {"plan", "build-branch"}


@pytest.mark.component
def test_list_scope_graph_returns_only_graph_rows_and_never_calls_the_hub(tmp_path: Path) -> None:
    """The scoped read is the property under test, asserted independently of ``get``'s
    own version below — a filter applied after an unconditional proxy call would satisfy
    the row-shape assertion above while still round-tripping to the hub."""
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _seed_graph_artifacts(store)
    seen: list[str] = []
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE), seen)
    with TestClient(app) as client:
        resp = client.get(
            "/api/leases/lease_1/artifacts", params={"scope": "graph"}, headers={"X-Blizzard-Lease-Token": _TOKEN}
        )
    assert resp.status_code == 200, resp.text
    assert [a["name"] for a in resp.json()] == ["docket"]
    assert seen == []


@pytest.mark.component
def test_get_scope_graph_resolves_from_the_store_and_never_calls_the_hub(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _seed_graph_artifacts(store)
    seen: list[str] = []
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE), seen)
    with TestClient(app) as client:
        resp = client.get(
            "/api/leases/lease_1/artifacts/docket",
            params={"scope": "graph"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "scope": "graph",
        "name": "docket",
        "kind": "asset",
        "node_name": None,
        "epoch": None,
        "repo": None,
        "branch_name": None,
        "commit_hash": None,
        "content": "the docket text",
    }
    assert seen == []


@pytest.mark.component
def test_get_scope_graph_404s_naming_the_pinned_mint_without_falling_back_to_node(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _seed_graph_artifacts(store)
    seen: list[str] = []
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE), seen)
    with TestClient(app) as client:
        resp = client.get(
            "/api/leases/lease_1/artifacts/plan",
            params={"scope": "graph"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 404
    assert "gr_1" in resp.json()["detail"]
    assert seen == []


@pytest.mark.component
def test_get_node_under_scope_graph_is_refused_not_silently_dropped(tmp_path: Path) -> None:
    """``node`` narrows to node scope and ``scope=graph`` excludes node scope, so the pair names
    two different searches — answering either discards a flag the caller passed."""
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _seed_graph_artifacts(store)
    seen: list[str] = []
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE), seen)
    with TestClient(app) as client:
        resp = client.get(
            "/api/leases/lease_1/artifacts/docket",
            params={"node": "plan", "scope": "graph"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "--node" in detail and "--scope graph" in detail
    assert seen == []


_ENVELOPE_WITH_A_GRAPH_COLLIDING_NAME: dict[str, object] = {
    **_ENVELOPE,
    "artifacts": [
        {"name": "docket", "kind": "asset", "node_name": "plan", "epoch": 1, "content": "a node's own docket"},
    ],
}


@pytest.mark.component
def test_get_bare_name_ambiguous_across_both_scopes_names_them(tmp_path: Path) -> None:
    """A cross-graph migration can leave a node artifact colliding with a graph
    declaration — the mint-time collision check only protects the same-graph case, so this
    route owns its own 409 rather than assuming the collision was already impossible."""
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _seed_graph_artifacts(store)
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE_WITH_A_GRAPH_COLLIDING_NAME))
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts/docket", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "graph" in detail and "plan" in detail
    assert "--scope" in detail and "--node" in detail


@pytest.mark.component
def test_get_node_alone_settles_a_cross_scope_collision(tmp_path: Path) -> None:
    """``node`` names a *producing* node, and a graph declaration has none — so supplying it
    is already a narrowing to node scope, and must resolve the collision above without the
    caller also having to pass ``scope``."""
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _seed_graph_artifacts(store)
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE_WITH_A_GRAPH_COLLIDING_NAME))
    with TestClient(app) as client:
        resp = client.get(
            "/api/leases/lease_1/artifacts/docket",
            params={"node": "plan"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scope"] == "node"
    assert resp.json()["content"] == "a node's own docket"


@pytest.mark.component
def test_get_409_across_graph_and_system_never_advises_node(tmp_path: Path) -> None:
    """A graph declaration and a system artifact can collide with no node candidate in the
    mix at all — neither scope has a producing node, so advising ``--node`` would send the
    caller toward an unrelated 404 rather than a resolution; only ``--scope`` remains."""
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _seed_graph_artifacts(store)
    _stub_hub(
        app,
        _FakeHubResponse(200, _ENVELOPE),
        system_get=_FakeHubResponse(200, {"name": "docket", "content": "blizzard's own docket"}),
    )
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts/docket", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "graph" in detail and "system" in detail
    assert "--scope" in detail
    assert "--node" not in detail


@pytest.mark.component
def test_get_409_names_only_the_levers_the_caller_has_left(tmp_path: Path) -> None:
    """A caller who already passed ``scope=node`` and still hit several producing nodes has
    only ``--node`` left; naming ``--scope`` again is advice they cannot act on."""
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE_WITH_DUPLICATE_NAME))
    with TestClient(app) as client:
        resp = client.get(
            "/api/leases/lease_1/artifacts/retrospective",
            params={"scope": "node"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "--node" in detail
    assert "--scope" not in detail


@pytest.mark.component
def test_get_404_after_searching_both_scopes_says_so(tmp_path: Path) -> None:
    """A bare miss searched the mint's declarations too, so the detail names that mint —
    rather than reporting only the node-step it also failed to find the name in."""
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _seed_graph_artifacts(store)
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE))
    with TestClient(app) as client:
        bare = client.get("/api/leases/lease_1/artifacts/ghost", headers={"X-Blizzard-Lease-Token": _TOKEN})
        narrowed = client.get(
            "/api/leases/lease_1/artifacts/ghost",
            params={"node": "plan"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert bare.status_code == 404 and narrowed.status_code == 404
    assert "gr_1" in bare.json()["detail"]
    # The narrowed miss never looked at graph scope, so it claims no graph-scoped search.
    assert "gr_1" not in narrowed.json()["detail"]


# System scope — a published document, always a hub-proxied forward, never runner-local


_SYSTEM_ARTIFACT = {"name": "garden/finding-format", "content": "the format text"}


@pytest.mark.component
def test_list_scope_system_forwards_to_the_hub_and_returns_the_set(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    seen: list[str] = []
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE), seen, system_list=_FakeHubResponse(200, [_SYSTEM_ARTIFACT]))
    with TestClient(app) as client:
        resp = client.get(
            "/api/leases/lease_1/artifacts", params={"scope": "system"}, headers={"X-Blizzard-Lease-Token": _TOKEN}
        )
    assert resp.status_code == 200, resp.text
    assert seen == [_SYSTEM_ARTIFACTS_PATH]
    body = resp.json()
    assert body == [
        {
            "scope": "system",
            "name": "garden/finding-format",
            "kind": "asset",
            "node_name": None,
            "epoch": None,
            "repo": None,
            "branch_name": None,
            "commit_hash": None,
            "content": "the format text",
        }
    ]


@pytest.mark.component
def test_get_scope_system_returns_the_named_artifact(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    seen: list[str] = []
    _stub_hub(
        app,
        _FakeHubResponse(200, _ENVELOPE),
        seen,
        system_get=_FakeHubResponse(200, _SYSTEM_ARTIFACT),
    )
    with TestClient(app) as client:
        resp = client.get(
            f"/api/leases/lease_1/artifacts/{quote('garden/finding-format', safe='/')}",
            params={"scope": "system"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 200, resp.text
    assert seen == [f"{_SYSTEM_ARTIFACTS_PATH}/garden/finding-format"]
    assert resp.json()["scope"] == "system"
    assert resp.json()["content"] == "the format text"


@pytest.mark.component
def test_get_scope_system_404_for_an_unpublished_name(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE))
    with TestClient(app) as client:
        resp = client.get(
            "/api/leases/lease_1/artifacts/ghost",
            params={"scope": "system"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]


@pytest.mark.component
def test_get_node_under_scope_system_is_refused_not_silently_dropped(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    seen: list[str] = []
    _stub_hub(app, _FakeHubResponse(200, _ENVELOPE), seen)
    with TestClient(app) as client:
        resp = client.get(
            "/api/leases/lease_1/artifacts/docket",
            params={"node": "plan", "scope": "system"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "--node" in detail and "--scope system" in detail
    assert seen == []


_ENVELOPE_WITH_A_SYSTEM_COLLIDING_NAME: dict[str, object] = {
    **_ENVELOPE,
    "artifacts": [
        {"name": "docket", "kind": "asset", "node_name": "plan", "epoch": 1, "content": "a node's own docket"},
    ],
}


@pytest.mark.component
def test_get_bare_name_ambiguous_across_node_and_system_names_both(tmp_path: Path) -> None:
    """The same read-time collision D3 resolves for a graph declaration applies to a
    system artifact's global name — a node's own ``produces:`` output can collide with it,
    and the route owns the 409 rather than assuming it away."""
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(
        app,
        _FakeHubResponse(200, _ENVELOPE_WITH_A_SYSTEM_COLLIDING_NAME),
        system_get=_FakeHubResponse(200, {"name": "docket", "content": "blizzard's own docket"}),
    )
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/artifacts/docket", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert "system" in detail and "plan" in detail
    assert "--scope" in detail and "--node" in detail


@pytest.mark.component
def test_get_node_alone_settles_a_system_collision(tmp_path: Path) -> None:
    """``node`` names a *producing* node, and a system artifact has none — so supplying it
    is already a narrowing to node scope, resolving the collision above on its own."""
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(
        app,
        _FakeHubResponse(200, _ENVELOPE_WITH_A_SYSTEM_COLLIDING_NAME),
        system_get=_FakeHubResponse(200, {"name": "docket", "content": "blizzard's own docket"}),
    )
    with TestClient(app) as client:
        resp = client.get(
            "/api/leases/lease_1/artifacts/docket",
            params={"node": "plan"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scope"] == "node"
    assert resp.json()["content"] == "a node's own docket"
