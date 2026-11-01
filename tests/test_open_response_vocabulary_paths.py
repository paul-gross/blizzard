"""An open response vocabulary end to end (``bzh:open-response-vocabulary``, component tier): a value this build does
not know parses, reaches the drain as an accepted outcome, and passes a runner proxy route through raw; a request
carrying one is still refused."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import httpx2
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel
from structlog.testing import capture_logs

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.project_colors import ProjectColor
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.hub.node_steps import apply_reply_of, completion_submission
from blizzard.runner.hub.outbound import COMPLETION_KIND
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle.drain import OutboundDrain
from blizzard.runner.node_steps.chunk_state import ChunkState
from blizzard.runner.node_steps.submissions import Completion
from blizzard.wire.envelope import ApplyResponse
from blizzard.wire.project import ProjectView
from blizzard.wire.vocabulary import OPEN, OPEN_MARK
from tests.config_inputs import create_runner_app
from tests.runner_client import runner_client
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    make_context,
    make_store,
    no_retry_clock,
)

pytestmark = pytest.mark.component

_NOW = datetime(2026, 10, 21, 12, 0, 0, tzinfo=UTC)


class _Shade(StrEnum):
    LIGHT = "light"
    DARK = "dark"


class _ShadeView(BaseModel):
    shade: Annotated[_Shade | Unrecognized, OPEN]
    shades: list[Annotated[_Shade | Unrecognized, OPEN]] = []


class _ShadeRequest(BaseModel):
    shade: _Shade


@pytest.mark.unit
def test_an_open_field_parses_an_unknown_value_raw_and_a_known_one_typed() -> None:
    view = _ShadeView.model_validate({"shade": "gained", "shades": ["dark", "also-gained"]})
    assert view.shade == Unrecognized("gained")
    assert view.shades == [_Shade.DARK, Unrecognized("also-gained")]


@pytest.mark.unit
def test_an_open_field_serializes_an_unknown_value_back_raw_in_both_modes() -> None:
    view = _ShadeView.model_validate({"shade": "gained", "shades": ["dark"]})
    assert json.loads(view.model_dump_json()) == {"shade": "gained", "shades": ["dark"]}
    assert _ShadeView.model_validate(view.model_dump()) == view


@pytest.mark.unit
def test_an_open_field_still_refuses_a_value_that_is_not_a_string() -> None:
    with pytest.raises(ValueError, match="shade"):
        _ShadeView.model_validate({"shade": 3})


@pytest.mark.unit
def test_an_open_field_keeps_its_vocabulary_schema_and_carries_the_open_mark() -> None:
    app = FastAPI()

    @app.get("/shade", response_model=_ShadeView)
    def shade() -> _ShadeView:
        return _ShadeView(shade=_Shade.LIGHT)

    schemas = app.openapi()["components"]["schemas"]
    assert schemas["_ShadeView"]["properties"]["shade"] == {"$ref": "#/components/schemas/_Shade", OPEN_MARK: True}
    assert schemas["_Shade"]["enum"] == ["light", "dark"]


def test_a_route_returning_an_open_view_passes_an_unknown_value_through_raw() -> None:
    app = FastAPI()
    upstream = {"shade": "gained", "shades": ["light", "also-gained"]}

    @app.get("/shade", response_model=_ShadeView)
    def shade() -> _ShadeView:
        return _ShadeView.model_validate(upstream)

    assert TestClient(app).get("/shade").json() == upstream


def test_the_runner_chunk_proxy_passes_unknown_values_through_raw(tmp_path: Path) -> None:
    detail = {
        "chunk_id": "ch_1",
        "graph_id": "g_1",
        "status": "gained",
        "current_node_id": None,
        "latest_epoch": 1,
        "artifacts": [
            {"key": "a.1", "kind": "gained-kind", "name": "a", "node_id": "nd_1", "node_name": "n", "epoch": 1}
        ],
    }
    hub = httpx2.Client(transport=httpx2.MockTransport(lambda _request: httpx2.Response(200, json=detail)))
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url="http://hub.local:8421")
    app = create_runner_app(config, hub_proxy_client=hub, hub_retry_clock=no_retry_clock())

    resp = runner_client(app).get("/api/chunks/ch_1")

    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "gained"
    assert resp.json()["artifacts"][0]["kind"] == "gained-kind"


def test_a_request_carrying_an_unknown_value_is_refused_with_422() -> None:
    app = FastAPI()

    @app.post("/shade")
    def shade(body: _ShadeRequest) -> dict[str, str]:
        return {"shade": body.shade}

    client = TestClient(app)
    assert client.post("/shade", json={"shade": "dark"}).status_code == 200
    assert client.post("/shade", json={"shade": "gained"}).status_code == 422


def _seed_running_lease(store: SqlAlchemyRunnerStore) -> None:
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            session_name="main",
            created_at=_NOW,
        )
    )
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    submission = Completion(choice="pass", epoch=1, from_node_id="nd_build")
    store.enqueue_outbound(
        kind=COMPLETION_KIND,
        chunk_id="ch_1",
        lease_id="lease_1",
        payload=json.dumps({"submission": completion_submission(submission).model_dump(mode="json")}),
        created_at=_NOW,
    )


def test_the_drain_accepts_an_unknown_apply_outcome_and_holds_the_binding(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_running_lease(store)
    hub = FakeHub()
    hub.chunks["ch_1"] = ChunkState(chunk_id="ch_1", status=ChunkStatus.RUNNING, latest_epoch=1, route_runner_id="r1")
    hub.apply_responses = [apply_reply_of(ApplyResponse.model_validate({"outcome": "gained", "detail": "newer"}))]
    handle = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=handle, verdict=None),
        probe=FakeProbe(alive={(100, "start-100")}),
    )

    with capture_logs() as logs:
        OutboundDrain(ctx).run()

    assert store.pending_outbound() == []  # consumed, not retried
    closed = {record.lease.lease_id: record.reason for record in store.list_closed_leases(10)}
    assert closed["lease_1"] == "transitioned"  # applied, not failed
    assert [b.environment_id for b in store.bindings_for_chunk("ch_1")] == ["e1"]  # held for the hub's status
    assert any(log.get("outcome") == "gained" for log in logs)


@pytest.mark.unit
def test_a_project_colour_parses_a_hex_colour_as_one_and_an_unknown_palette_name_raw() -> None:
    def colour(value: str) -> object:
        return ProjectView.model_validate(
            {"project_id": "pj_1", "slug": "p", "name": "P", "description": "", "color": value, "revision": 1}
            | {"created_at": "2026-10-21T12:00:00Z"}
        ).color

    assert colour("#a1b2c3") == "#a1b2c3"
    assert colour("red") is ProjectColor.RED
    assert colour("mauve") == Unrecognized("mauve")
