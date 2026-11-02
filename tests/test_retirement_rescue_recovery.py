"""An interrupted runner sweep completes before a released runner is rescued."""

import json
from pathlib import Path
from typing import cast

import pytest

from blizzard.hub.domain.runners.registration import IWriteRunnerRegistry, RunnerNotRetired, RunnerRetirementChanged
from tests.support import build_hub
from tests.test_runner_retirement import _held_chunk, _register

pytestmark = pytest.mark.component


def test_rescue_finishes_interrupted_host_runner_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    chunk_id = _held_chunk(hub)
    host_id = hub.client.post("/api/hosts", json={"name": "host"}).json()["host_id"]
    assert hub.client.post(f"/api/hosts/{host_id}/adoptions", json={"runner_ids": ["runner-a"]}).status_code == 200
    registry = cast(IWriteRunnerRegistry, hub.services.registry)
    original = registry.record_retirement

    def crash_after_runner_commit(*args, **kwargs) -> None:
        original(*args, **kwargs)
        raise RuntimeError("interrupted sweep")

    monkeypatch.setattr(registry, "record_retirement", crash_after_runner_commit)
    with pytest.raises(RuntimeError, match="interrupted sweep"):
        hub.client.post(f"/api/hosts/{host_id}/retire", json={"by": "op", "force": True})
    monkeypatch.setattr(registry, "record_retirement", original)
    assert hub.services.chunks.route.route_of(chunk_id) is not None
    before = len(hub.events.snapshot())
    refused = hub.client.post("/api/runners/runner-a/reinstate", json={"by": "op"})
    assert refused.status_code == 409
    assert hub.services.chunks.route.route_of(chunk_id) is not None
    assert len(hub.events.snapshot()) == before
    release = hub.client.post(f"/api/hosts/{host_id}/releases", json={"runner_ids": ["runner-a"], "by": "op"})
    assert release.status_code == 200, release.text
    before = len(hub.events.snapshot())
    rescue = hub.client.post("/api/runners/runner-a/reinstate", json={"by": "op"})
    assert rescue.status_code == 200, rescue.text
    assert hub.services.chunks.route.route_of(chunk_id) is None
    events = hub.events.snapshot()[before:]
    assert [event.type for event in events] == ["chunk-changed", "queue-changed", "runner-changed"]
    chunk = json.loads(events[0].data)
    assert chunk["chunk_id"] == chunk_id
    assert chunk["status"] == "ready"
    assert chunk["cause"] == "detached"
    assert json.loads(events[2].data)["kind"] == "reinstated"


@pytest.mark.parametrize(
    "error", [RunnerNotRetired("runner-a"), RunnerRetirementChanged("runner-a"), RuntimeError("failed")]
)
def test_failed_rescue_publishes_committed_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    chunk_id = _held_chunk(hub)
    registry = cast(IWriteRunnerRegistry, hub.services.registry)
    original = registry.record_retirement

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("interrupted")

    monkeypatch.setattr(registry, "record_retirement", interrupted)
    with pytest.raises(RuntimeError, match="interrupted"):
        hub.client.post("/api/runners/runner-a/retire", json={"by": "op", "force": True})

    def fail_lifecycle(*args, **kwargs):
        raise error

    monkeypatch.setattr(registry, "record_lifecycle", fail_lifecycle)
    before = len(hub.events.snapshot())
    if isinstance(error, RunnerNotRetired):
        response = hub.client.post("/api/runners/runner-a/reinstate", json={"by": "op"})
        assert response.status_code == 409
        assert response.json()["detail"] == str(error)
    else:
        with pytest.raises(type(error), match=str(error)):
            hub.client.post("/api/runners/runner-a/reinstate", json={"by": "op"})
    assert hub.services.chunks.route.route_of(chunk_id) is None
    events = hub.events.snapshot()[before:]
    assert [event.type for event in events] == ["chunk-changed", "queue-changed"]
    chunk = json.loads(events[0].data)
    assert chunk["chunk_id"] == chunk_id
    assert chunk["cause"] == "detached"
