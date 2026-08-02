"""``GET /api/leases/{id}/history`` and its pure projection ``history_rows`` (issue #237).

Unit tier: :func:`blizzard.wire.history.history_rows` over a fixture
:class:`~blizzard.wire.history.ChunkHistoryView` — a bounced attempt that produced no
artifact still becomes a row, a migration becomes its own row, and everything merges
oldest-first. Component tier: exercised over a real store via ``TestClient``, mirroring
``tests/test_runner_artifacts_api.py`` (the hub is reached through a stubbed
``httpx.get``, so the forward, its status pass-through, and the ``502`` on an
unreachable hub are all asserted against the real controller).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import blizzard.runner.api.history as history_route
from blizzard.hub.domain.enrollment import hash_token
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.store.repository import NewLease
from blizzard.wire.chunk import BounceView, MigrationView, TransitionView
from blizzard.wire.history import ChunkHistoryView, history_rows
from tests.runner_fakes import make_store

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 21, 12, 0, 0, tzinfo=UTC)
_TOKEN = "the-lease-token"
_HUB_URL = "http://hub.local:8421"
_CHUNK = "ch_1"

# The hub's ``ChunkDetail`` payload the proxy forwards to — a chunk that bounced once
# (no artifact for the bounced attempt) and migrated once, so all three row kinds are
# proven at once. Deliberately out of oldest-first order in the source lists — the
# assertions prove the merge, not an already-sorted input.
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


# --------------------------------------------------------------------------- #
# Unit — the pure projection
# --------------------------------------------------------------------------- #


def test_history_rows_merges_all_three_kinds_oldest_first() -> None:
    detail = ChunkHistoryView.model_validate(_DETAIL)
    rows = history_rows(detail)
    assert [r.recorded_at for r in rows] == [
        "2026-07-21T09:00:00+00:00",
        "2026-07-21T10:00:00+00:00",
        "2026-07-21T11:00:00+00:00",
        "2026-07-21T12:00:00+00:00",
    ]
    assert [r.kind for r in rows] == ["migration", "transition", "bounce", "transition"]


def test_a_bounced_attempt_with_no_artifact_is_still_a_row() -> None:
    """#237 AC3: a bounce that produced no artifact anywhere in the envelope still
    appears on the timeline — it carries its own kick-back cause."""
    detail = ChunkHistoryView(
        bounces=[BounceView(cause="conflict", envelope="{}", recorded_at="2026-07-21T11:00:00+00:00")]
    )
    rows = history_rows(detail)
    assert len(rows) == 1
    assert rows[0].kind == "bounce"
    assert rows[0].cause == "conflict"
    assert rows[0].from_node is None and rows[0].to_node is None


def test_a_migration_becomes_its_own_row_with_a_graph_hop_label() -> None:
    detail = ChunkHistoryView(
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
        ]
    )
    rows = history_rows(detail)
    assert len(rows) == 1
    assert rows[0].kind == "migration"
    assert rows[0].from_node == "triage-graph/triage"
    assert rows[0].to_node == "adv-dwf/build"
    assert rows[0].detail == "authored-edge"
    assert rows[0].epoch is None


def test_a_transition_row_carries_epoch_and_choice() -> None:
    detail = ChunkHistoryView(
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
        ]
    )
    rows = history_rows(detail)
    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "transition"
    assert row.from_node == "review" and row.to_node == "build"
    assert row.choice == "fail"
    assert row.epoch == 2
    assert row.graph_name == "adv-dwf"


# --------------------------------------------------------------------------- #
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
    store.record_lease_token(str(fields["lease_id"]), hash_token(_TOKEN), _NOW)


def _stub_hub(monkeypatch: pytest.MonkeyPatch, response: _FakeHubResponse, seen: list[str] | None = None) -> None:
    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        if seen is not None:
            seen.append(url)
        return response

    monkeypatch.setattr(history_route.httpx, "get", fake_get)


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

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        seen_headers.append(dict(headers))
        return _FakeHubResponse(200, _DETAIL)

    monkeypatch.setattr(history_route.httpx, "get", fake_get)
    with TestClient(create_app(config, runner_store=store)) as client:
        resp = client.get("/api/leases/lease_1/history", headers={"X-Blizzard-Lease-Token": _TOKEN})
    assert resp.status_code == 200, resp.text
    assert seen_headers == [{"Authorization": "Bearer hub-tok"}]


@pytest.mark.component
def test_502_when_the_hub_is_unreachable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    app, store = _app_with_store(tmp_path)
    _seed_lease(store)

    def fake_get(url: str, *, headers: dict[str, str], timeout: float) -> _FakeHubResponse:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(history_route.httpx, "get", fake_get)
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
