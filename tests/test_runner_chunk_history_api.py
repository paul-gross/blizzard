"""``GET /api/leases/{id}/history`` and its pure projection ``ChunkHistoryView.rows`` (issue #237).

Unit tier: ``ChunkHistoryView.rows`` over a fixture — a bounced attempt
that produced no artifact still becomes a row, a migration becomes its own row, and
everything merges oldest-first. Component tier: exercised over a real store via
``TestClient``, the hub reached through a stubbed ``httpx.get``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import blizzard.runner.api.hub_proxy as hub_proxy
from blizzard.hub.domain.enrollment import TokenHash
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.store.repository import NewLease
from blizzard.wire.chunk import BounceView, MigrationView, TransitionView
from blizzard.wire.history import ChunkHistoryView
from tests.runner_fakes import make_store
from tests.support import build_hub, pointer_token, report_lease

_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)
_TOKEN = "the-lease-token"
_HUB_URL = "http://hub.local:8421"
_CHUNK = "ch_1"

# The hub's ``ChunkDetail`` payload: a chunk that bounced once and migrated once, so all
# three row kinds are proven at once, deliberately out of oldest-first source order.
_DETAIL: dict[str, object] = {
    "chunk_id": _CHUNK,
    "graph_id": "gr_1",
    "status": "running",
    "current_node_id": "nd_build",
    "latest_epoch": 3,
    "history": [
        {
            "from_node_id": "nd_build",
            "from_node_name": "build",
            "to_node_id": "nd_review",
            "to_node_name": "review",
            "choice_name": "ready",
            "epoch": 1,
            "recorded_at": "2026-07-21T10:00:00+00:00",
            "graph_id": "gr_1",
            "graph_name": "adv-dwf",
        },
        {
            "from_node_id": "nd_review",
            "from_node_name": "review",
            "to_node_id": "nd_build",
            "to_node_name": "build",
            "choice_name": "fail",
            "epoch": 2,
            "recorded_at": "2026-07-21T12:00:00+00:00",
            "graph_id": "gr_1",
            "graph_name": "adv-dwf",
        },
    ],
    "migrations": [
        {
            "from_node_id": "nd_triage",
            "from_node_name": "triage",
            "from_graph_id": "gr_0",
            "from_graph_name": "triage-graph",
            "to_graph_id": "gr_1",
            "to_graph_name": "adv-dwf",
            "landed_node_id": "nd_build",
            "landed_node_name": "build",
            "choice_name": "route",
            "source": "authored-edge",
            "recorded_at": "2026-07-21T09:00:00+00:00",
        }
    ],
    "bounces": [
        {
            "cause": "conflict",
            "envelope": '{"base": "master"}',
            "recorded_at": "2026-07-21T11:00:00+00:00",
        }
    ],
}


# Unit — the pure projection
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_history_rows_merges_all_three_kinds_oldest_first() -> None:
    detail = ChunkHistoryView.model_validate(_DETAIL)
    rows = detail.rows()
    assert [r.recorded_at for r in rows] == [
        "2026-07-21T09:00:00+00:00",
        "2026-07-21T10:00:00+00:00",
        "2026-07-21T11:00:00+00:00",
        "2026-07-21T12:00:00+00:00",
    ]
    assert [r.kind for r in rows] == ["migration", "transition", "bounce", "transition"]


@pytest.mark.unit
def test_a_bounced_attempt_with_no_artifact_is_still_a_row() -> None:
    """#237 AC3: a bounce that produced no artifact anywhere in the envelope still
    appears on the timeline — it carries its own kick-back cause."""
    detail = ChunkHistoryView(
        history=[],
        migrations=[],
        bounces=[BounceView(cause="conflict", envelope="{}", recorded_at="2026-07-21T11:00:00+00:00")],
    )
    rows = detail.rows()
    assert len(rows) == 1
    assert rows[0].kind == "bounce"
    assert rows[0].cause == "conflict"
    assert rows[0].from_node is None and rows[0].to_node is None


@pytest.mark.unit
def test_a_migration_becomes_its_own_row_with_a_graph_hop_label() -> None:
    detail = ChunkHistoryView(
        history=[],
        bounces=[],
        migrations=[
            MigrationView(
                from_node_id="nd_triage",
                from_node_name="triage",
                from_graph_id="gr_0",
                from_graph_name="triage-graph",
                to_graph_id="gr_1",
                to_graph_name="adv-dwf",
                landed_node_id="nd_build",
                landed_node_name="build",
                choice_name="route",
                source="authored-edge",
                recorded_at="2026-07-21T09:00:00+00:00",
            )
        ],
    )
    rows = detail.rows()
    assert len(rows) == 1
    assert rows[0].kind == "migration"
    assert rows[0].from_node == "triage-graph/triage"
    assert rows[0].to_node == "adv-dwf/build"
    assert rows[0].detail == "authored-edge"
    assert rows[0].epoch is None


@pytest.mark.unit
def test_a_transition_row_carries_epoch_and_choice() -> None:
    detail = ChunkHistoryView(
        migrations=[],
        bounces=[],
        history=[
            TransitionView(
                from_node_id="nd_review",
                from_node_name="review",
                to_node_id="nd_build",
                to_node_name="build",
                choice_name="fail",
                epoch=2,
                recorded_at="2026-07-21T12:00:00+00:00",
                graph_id="gr_1",
                graph_name="adv-dwf",
            )
        ],
    )
    rows = detail.rows()
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "transition"
    assert row.from_node == "review" and row.to_node == "build"
    assert row.choice == "fail"
    assert row.epoch == 2
    assert row.graph_name == "adv-dwf"


# Component — the lease-scoped, hub-proxying route
# --------------------------------------------------------------------------- #


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
        "epoch": 3,
        "runner_id": "runner-local",
        "retries_max": 2,
        "created_at": _NOW,
    }
    fields.update(overrides)
    store.record_lease(NewLease(**fields))  # type: ignore[arg-type]
    store.record_lease_token(str(fields["lease_id"]), TokenHash(_TOKEN).hex, _NOW)


def _stub_hub(monkeypatch: pytest.MonkeyPatch, response: _FakeHubResponse, seen: list[str] | None = None) -> None:
    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        if seen is not None:
            seen.append(url)
        return response

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)


@pytest.mark.component
def test_503_when_store_unwired(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url=_HUB_URL)
    with TestClient(create_app(config)) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 503


@pytest.mark.component
def test_404_for_an_unknown_lease(tmp_path: Path) -> None:
    app, _store = _app_with_store(tmp_path)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_ghost/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404


@pytest.mark.component
def test_403_for_a_missing_token(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/history")
    assert resp.status_code == 403


@pytest.mark.component
def test_403_for_a_wrong_token(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": "nope"})
    assert resp.status_code == 403


@pytest.mark.component
def test_a_closed_lease_is_404_not_403(tmp_path: Path) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    store.record_closure(lease_id="lease_1", chunk_id=_CHUNK, node_id="nd_build", reason="transitioned", closed_at=_NOW)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404


@pytest.mark.component
def test_an_open_takeover_authorizes_a_closed_reference_lease(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    _stub_hub(monkeypatch, _FakeHubResponse(200, _DETAIL))

    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": takeover_token})
    assert resp.status_code == 200, resp.text


@pytest.mark.component
def test_503_when_hub_unwired_even_for_an_authorized_lease(tmp_path: Path) -> None:
    """Authorization is resolved before the hub is consulted, so an unauthorized caller
    never learns the hub-wiring state — mirrors the artifacts proxy."""
    app, store = _app_with_store(tmp_path, hub_url="")
    _seed_lease(store)
    with TestClient(app) as client:
        authed = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
        unauthed = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": "nope"})
    assert authed.status_code == 503
    assert unauthed.status_code == 403


@pytest.mark.component
def test_forwards_to_the_hub_chunk_detail_route_and_returns_merged_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    seen: list[str] = []
    _stub_hub(monkeypatch, _FakeHubResponse(200, _DETAIL), seen)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert seen == [f"{_HUB_URL}/api/fleet/chunks/{_CHUNK}"]
    body = resp.json()
    assert [row["kind"] for row in body] == ["migration", "transition", "bounce", "transition"]


@pytest.mark.component
def test_forwards_the_runner_bearer_when_a_token_is_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The forward rides the runner principal's bearer (issue #86b) — the worker's own
    lease token never leaves the runner."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(
        root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", hub_url=_HUB_URL, hub_token="hub-tok"
    )
    _seed_lease(store)
    seen_headers: list[dict[str, str]] = []

    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        seen_headers.append(dict(headers))
        return _FakeHubResponse(200, _DETAIL)

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    with TestClient(create_app(config, runner_store=store)) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert seen_headers == [{"Authorization": "Bearer hub-tok"}]


@pytest.mark.component
def test_502_when_the_hub_is_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    app, store = _app_with_store(tmp_path)
    _seed_lease(store)

    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 502


@pytest.mark.component
def test_hub_status_passes_through_verbatim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app, store = _app_with_store(tmp_path)
    _seed_lease(store)
    _stub_hub(monkeypatch, _FakeHubResponse(404, {"detail": "no such chunk"}))
    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 404
    assert resp.json()["detail"] == "no such chunk"


# The full round trip — a real hub, a real bounce loop, matched row-for-row
# --------------------------------------------------------------------------- #

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


def _step(node_id: str, *, epoch: int, choice: str) -> dict:
    return {"choice": choice, "epoch": epoch, "runner_id": "r1", "from_node_id": node_id}


@pytest.mark.component
def test_a_workers_history_read_matches_the_transitions_the_hub_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#237 AC5: drive a chunk through a bounce loop against a real hub app, then read
    its history through the runner's proxy and assert row-for-row equality with the
    hub's own recorded ``ChunkDetail.history``."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _SCENARIO_YAML}).status_code == 201
    chunk_id = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": "1"})]}
    ).json()["chunk_id"]
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

    def fake_request(method: str, url: str, *, headers: dict[str, str], timeout: float) -> object:
        return hub.client.get(url.replace(_HUB_URL, ""))

    monkeypatch.setattr(hub_proxy.httpx, "request", fake_request)
    runner_store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    runner_store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id=chunk_id,
            graph_id=hub_detail["graph_id"],
            node_id=review_node_id_2,
            node_name="review",
            epoch=4,
            runner_id="runner-local",
            retries_max=2,
            created_at=_NOW,
        )
    )
    runner_store.record_lease_token("lease_1", TokenHash(_TOKEN).hex, _NOW)
    runner_config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", hub_url=_HUB_URL)
    with TestClient(create_app(runner_config, runner_store=runner_store)) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    worker_rows = resp.json()

    expected = [row.model_dump() for row in ChunkHistoryView.model_validate(hub_detail).rows()]
    assert worker_rows == expected
    assert len(worker_rows) == 4
    assert [r["kind"] for r in worker_rows] == ["transition", "transition", "transition", "transition"]
    fail_row = worker_rows[1]
    assert fail_row["choice"] == "fail" and fail_row["from_node"] == "review" and fail_row["to_node"] == "build"
