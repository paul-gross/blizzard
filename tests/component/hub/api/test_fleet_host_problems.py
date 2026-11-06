"""Host-facing refusals carry their problem type and the original detail."""

from pathlib import Path
from typing import Any

import pytest

from blizzard.hub.domain.execution.fleet import FleetService, RetireInterrupted, RetireOutcome
from blizzard.hub.domain.execution.host_facts import HostFactIngestService
from blizzard.hub.domain.runners.host import HostRetired
from blizzard.hub.domain.runners.host_report import HostReportService
from blizzard.hub.domain.runners.registration import RunnerLifecycleConflict
from blizzard.wire import problem as problem_types
from tests.support.hub_request_auth import bearer
from tests.support.scaffolding import build_hub


def _assert_problem(response: Any, code: int, kind: str, detail: str, path: str) -> None:
    assert response.status_code == code, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert (body["type"], body["status"], body["detail"], body["instance"]) == (kind, code, detail, path)


def test_unknown_project_and_runner(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = hub.client.post("/api/hosts", json={"name": "box"}).json()
    headers = bearer(host["token"])
    path = "/api/fleet/host/runners"
    response = hub.client.post(path, json={"name": "runner", "projects": ["missing"]}, headers=headers)
    assert response.status_code == 422
    assert response.json()["type"] == problem_types.UNKNOWN_SERVED_PROJECT
    assert response.json()["instance"] == path
    path += "/rn_missing/retire"
    response = hub.client.post(path, json={}, headers=headers)
    _assert_problem(response, 404, problem_types.RUNNER_NOT_FOUND, "unknown runner rn_missing", path)


@pytest.mark.parametrize("interrupted", [False, True])
def test_retirement_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupted: bool) -> None:
    hub = build_hub(tmp_path)
    host = hub.client.post("/api/hosts", json={"name": "box"}).json()
    headers = bearer(host["token"])
    runner = hub.client.post("/api/fleet/host/runners", json={"name": "runner"}, headers=headers).json()["runner_id"]
    conflict = RunnerLifecycleConflict(runner, expected=0)
    exc = (
        RetireInterrupted(RetireOutcome(fact_id=None, revocation_id=None, released=()), conflict)
        if interrupted
        else conflict
    )

    def refuse(self: FleetService, *args: Any, **kwargs: Any) -> Any:
        raise exc

    monkeypatch.setattr(FleetService, "retire", refuse)
    path = f"/api/fleet/host/runners/{runner}/retire"
    response = hub.client.post(path, json={}, headers=headers)
    kind = problem_types.RUNNER_RETIRE_INTERRUPTED if interrupted else problem_types.RUNNER_LIFECYCLE_CONFLICT
    _assert_problem(response, 409, kind, str(exc), path)


@pytest.mark.parametrize("lane", ["report", "events"])
def test_retired_host_race(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lane: str) -> None:
    hub = build_hub(tmp_path)
    host = hub.client.post("/api/hosts", json={"name": "box"}).json()
    exc = HostRetired(host["host_id"], action="test")

    def refuse(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise exc

    payload: dict[str, Any]
    if lane == "report":
        monkeypatch.setattr(HostReportService, "report", refuse)
        path = "/api/fleet/host"
        payload = {"name": "box"}
    else:
        monkeypatch.setattr(HostFactIngestService, "ingest", refuse)
        path = "/api/fleet/host/events"
        payload = {"facts": []}
    response = hub.client.post(path, json=payload, headers=bearer(host["token"]))
    _assert_problem(response, 403, problem_types.HOST_RETIRED, str(exc), path)
