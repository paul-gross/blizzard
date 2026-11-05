"""``GET /api/leases/{id}/history``.

Exercised over a real store via ``TestClient``, the hub reached through
a stubbed ``httpx2.Client``."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Unpack

import httpx2
from fastapi import FastAPI

from blizzard.foundation.tokens import TokenHash
from blizzard.runner.api.history import rows
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.leases.model import NewLease
from blizzard.wire.history import ChunkHistoryView
from tests.support.chunk_history_api import CHUNK, DETAIL
from tests.support.config_inputs import create_runner_app
from tests.support.factory_fields import NewLeaseFields
from tests.support.runner_client import runner_client
from tests.support.runner_fakes import SqlAlchemyRunnerStore, make_host_stores, make_store, make_stores, no_retry_clock
from tests.support.scaffolding import build_hub, make_ready, pointer_token, report_lease

_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)


_TOKEN = "the-lease-token"


_HUB_URL = "http://hub.local:8421"


class _HubRouter:
    """A late-bound handler behind the proxy's ``httpx2.Client`` — ``_app_with_store`` builds
    the client (and the app wired to it) before a test knows how the hub should answer, so
    ``_stub_hub`` arms this after the fact instead of monkeypatching a module-level function."""

    def __init__(self) -> None:
        self.handler: Callable[[httpx2.Request], httpx2.Response] = lambda request: httpx2.Response(
            500, json={"detail": f"hub not stubbed for {request.url}"}
        )

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        return self.handler(request)


def _app_with_store(tmp_path: Path, *, hub_url: str = _HUB_URL):
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", hub_url=hub_url)
    router = _HubRouter()
    app = create_runner_app(
        config,
        runner_stores=make_stores(store),
        host_stores=make_host_stores(store),
        hub_proxy_client=httpx2.Client(transport=httpx2.MockTransport(router)),
        hub_retry_clock=no_retry_clock(),
    )
    app.state.hub_router = router
    return app, store


def _seed_lease(store: SqlAlchemyRunnerStore, **overrides: Unpack[NewLeaseFields]) -> None:
    fields: NewLeaseFields = {
        "lease_id": "lease_1",
        "chunk_id": CHUNK,
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 3,
        "retries_max": 2,
        "created_at": _NOW,
    }
    fields.update(overrides)
    store.record_lease(NewLease(**fields))
    store.record_lease_token(fields["lease_id"], TokenHash(_TOKEN).hex, _NOW)


def _stub_hub(app: FastAPI, status_code: int, payload: object, seen: list[str] | None = None) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(str(request.url))
        return httpx2.Response(status_code, json=payload)

    app.state.hub_router.handler = handler


def test_503_when_store_unwired(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=_HUB_URL)
    with runner_client(create_runner_app(config)) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 503


def test_404_for_an_unknown_lease(tmp_path: Path) -> None:
    app, _store = _app_with_store(tmp_path)
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_ghost/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404


def test_403_for_a_missing_token(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/history")
    assert resp.status_code == 403


def test_403_for_a_wrong_token(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": "nope"})
    assert resp.status_code == 403


def test_a_closed_lease_is_404_not_403(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id=CHUNK, node_id="nd_build", reason="transitioned", closed_at=_NOW)
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404


def test_an_open_takeover_authorizes_a_closed_reference_lease(tmp_path: Path) -> None:
    """The worker-authorization resolver's other half: once an open
    takeover names the (now closed) reference lease, its re-minted token reaches
    this route the same as an ordinary active lease would."""
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id=CHUNK, node_id="nd_build", reason="escalated", closed_at=_NOW)
    takeover_token = "the-takeover-token"
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id=CHUNK,
        lease_id="lease_1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )
    store.record_lease_token("lease_1", TokenHash(takeover_token).hex, _NOW)
    _stub_hub(app, 200, DETAIL)

    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": takeover_token})
    assert resp.status_code == 200, resp.text


def test_503_when_hub_unwired_even_for_an_authorized_lease(tmp_path: Path) -> None:
    """Authorization is resolved before the hub is consulted, so an unauthorized caller
    never learns the hub-wiring state — mirrors the artifacts proxy."""
    app, store = _app_with_store(tmp_path, hub_url="")
    _seed_lease(store)
    with runner_client(app) as client:
        authed = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
        unauthed = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": "nope"})
    assert authed.status_code == 503
    assert unauthed.status_code == 403


def test_forwards_to_the_hub_chunk_detail_route_and_returns_merged_rows(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    seen: list[str] = []
    _stub_hub(app, 200, DETAIL, seen)
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert seen == [f"{_HUB_URL}/api/fleet/chunks/{CHUNK}"]
    body = resp.json()
    assert [row["kind"] for row in body] == ["migration", "transition", "bounce", "transition"]


def test_forwards_the_runner_bearer_when_a_token_is_configured(tmp_path: Path) -> None:
    """The forward rides the runner principal's bearer — the worker's own
    lease token never leaves the runner."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(
        root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", hub_url=_HUB_URL, hub_token="hub-tok"
    )
    _seed_lease(store)
    seen_headers: list[httpx2.Headers] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen_headers.append(request.headers)
        return httpx2.Response(200, json=DETAIL)

    hub_proxy_client = httpx2.Client(transport=httpx2.MockTransport(handler))
    with runner_client(
        create_runner_app(
            config,
            runner_stores=make_stores(store),
            host_stores=make_host_stores(store),
            hub_proxy_client=hub_proxy_client,
        )
    ) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert seen_headers[0]["Authorization"] == "Bearer hub-tok"


def test_502_when_the_hub_is_unreachable(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)

    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused")

    app.state.hub_router.handler = handler
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 502


def test_hub_status_passes_through_verbatim(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(app, 404, {"detail": "no such chunk"})
    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "no such chunk"


_SCENARIO_YAML = """
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
          to: review
        fail:
          description: Incomplete.
          to: build
  review:
    executor: runner
    prompt: |
      Review the change.
    judgement:
      prompt: |
        Assess the review.
      choices:
        pass:
          description: Approved.
          to: done
        fail:
          description: Needs another pass.
          to: build
"""


def _step(node_id: str, *, epoch: int, choice: str) -> dict[str, object]:
    return {"choice": choice, "epoch": epoch, "runner_id": "r1", "from_node_id": node_id}


def test_a_workers_history_read_matches_the_transitions_the_hub_recorded(tmp_path: Path) -> None:
    """#237 AC5: drive a chunk through a bounce loop against a real hub app, then read
    its history through the runner's proxy and assert row-for-row equality with the
    hub's own recorded ``ChunkDetail.history``."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _SCENARIO_YAML}).status_code == 201
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "1"})]}
    ).json()["chunk_id"]
    make_ready(hub, chunk_id)
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e1"]},
    )
    build_node_id = claim.json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)

    step1 = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions", json=_step(build_node_id, epoch=1, choice="pass")
    )
    review_node_id = step1.json()["next_envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=2, seq=2)

    step2 = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions", json=_step(review_node_id, epoch=2, choice="fail")
    )
    build_node_id_2 = step2.json()["next_envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=3, seq=3)

    step3 = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions", json=_step(build_node_id_2, epoch=3, choice="pass")
    )
    review_node_id_2 = step3.json()["next_envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=4, seq=4)

    step4 = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions", json=_step(review_node_id_2, epoch=4, choice="pass")
    )
    assert step4.json()["outcome"] == "done"

    hub_detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert len(hub_detail["history"]) == 4
    assert hub_detail["artifacts"] == []  # the review-fail attempt produced no artifact anywhere

    def handler(request: httpx2.Request) -> httpx2.Response:
        return hub.client.get(str(request.url).replace(_HUB_URL, ""))

    hub_proxy_client = httpx2.Client(transport=httpx2.MockTransport(handler))
    runner_store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    runner_store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id=chunk_id,
            graph_id=hub_detail["graph_id"],
            node_id=review_node_id_2,
            node_name="review",
            epoch=4,
            retries_max=2,
            created_at=_NOW,
        )
    )
    runner_store.record_lease_token("lease_1", TokenHash(_TOKEN).hex, _NOW)
    runner_config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", hub_url=_HUB_URL)
    with runner_client(
        create_runner_app(
            runner_config,
            runner_stores=make_stores(runner_store),
            host_stores=make_host_stores(runner_store),
            hub_proxy_client=hub_proxy_client,
        )
    ) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    worker_rows = resp.json()

    expected = [row.model_dump() for row in rows(ChunkHistoryView.model_validate(hub_detail))]
    assert worker_rows == expected
    assert len(worker_rows) == 4
    assert [r["kind"] for r in worker_rows] == ["transition", "transition", "transition", "transition"]
    fail_row = worker_rows[1]
    assert fail_row["choice"] == "fail" and fail_row["from_node"] == "review" and fail_row["to_node"] == "build"
