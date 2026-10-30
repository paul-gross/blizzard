"""A runner's bindings and a project-narrowed peek, built from the real wire models and sent to a live mock hub — the
service tier's check that the mock hub still speaks the shapes the real hub accepts and answers."""

from __future__ import annotations

import httpx2
import pytest

from blizzard.wire.queue import QueuePeekRequest, QueuePeekResponse
from blizzard.wire.runner import (
    RunnerBinding,
    RunnerCapability,
    RunnerRegistrationRequest,
    RunnerRegistrationResponse,
)
from tests.mock_fleet_support import mock_hub, mock_hub_chunk_spec, require_mock_fleet, service_gate
from tests.support import free_port

pytestmark = [pytest.mark.service, service_gate]

_CAPABILITIES = [RunnerCapability(harness_id="claude", default=True)]
_REPORT = {
    "name": "box",
    "workspaces": [{"workspace_id": "ws-a", "env_capacity": 2}],
    "subscriptions": [{"slug": "max", "name": "Max", "provider": "anthropic"}],
}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_chunk(hub: httpx2.Client, ref: str, project_id: str | None = None) -> str:
    spec = mock_hub_chunk_spec(ref)
    if project_id is not None:
        spec["project_id"] = project_id
    seeded = hub.post("/_seed/chunk", json=spec)
    assert seeded.status_code == 201, seeded.text
    return str(seeded.json()["chunk_id"])


def _peek(hub: httpx2.Client, token: str, request: QueuePeekRequest) -> list[str]:
    resp = hub.post("/api/fleet/queue/peek", json=request.model_dump(), headers=_bearer(token))
    assert resp.status_code == 200, resp.text
    return [entry.chunk_id for entry in QueuePeekResponse.model_validate(resp.json()).entries]


def test_a_bindings_registration_and_a_project_narrowed_peek_run_against_the_mock_hub() -> None:
    bin_dir = require_mock_fleet()
    with mock_hub(bin_dir, free_port()) as hub:
        web = hub.post("/_seed/projects", json={"slug": "web", "name": "Web"})
        assert web.status_code == 201, web.text
        web_chunk = _seed_chunk(hub, "1", web.json()["project_id"])
        default_chunk = _seed_chunk(hub, "2")
        host = hub.post("/api/hosts", json={"name": "box"}).json()
        runner = hub.post("/api/runners", json={"name": "r", "host_id": host["host_id"]}).json()
        assert hub.post("/api/fleet/host", json=_REPORT, headers=_bearer(host["token"])).status_code == 200
        registration = RunnerRegistrationRequest(
            workspace_id="ws-a",
            capabilities=_CAPABILITIES,
            bindings=[
                RunnerBinding(project="default", workspace_id="ws-a", agents=1),
                RunnerBinding(project="web", workspace_id="ws-x", agents=1),
            ],
            subscription="max",
        )

        registered = hub.post("/api/fleet/runners", json=registration.model_dump(), headers=_bearer(runner["token"]))
        assert registered.status_code == 201, registered.text
        answer = RunnerRegistrationResponse.model_validate(registered.json())
        everything = _peek(hub, runner["token"], QueuePeekRequest(capabilities=_CAPABILITIES))
        web_only = _peek(hub, runner["token"], QueuePeekRequest(capabilities=_CAPABILITIES, project="web"))

    assert [p.entry for p in answer.projects or []] == ["default", "web"]
    assert [(b.project, b.workspace_unreported) for b in answer.bindings or []] == [("default", False), ("web", True)]
    assert (answer.subscription, answer.subscription_unrostered) == ("max", False)
    assert everything == [default_chunk]
    assert web_only == []
    assert web_chunk not in everything
