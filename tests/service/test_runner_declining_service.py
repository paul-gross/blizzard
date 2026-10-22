"""A runner's declining report against a running hub (service tier): a registration naming no ``declining`` — a runner
that predates it — reads as declining nothing; one naming reasons shows them, superseded whole by the next
registration; and a hub built before the field parses the same body and admits the runner as it always did. Run with
``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from tests.fleet_launchers import run_forge, run_hub
from tests.service.support import service_gate
from tests.service.test_hub_service import _stack

pytestmark = [pytest.mark.service, service_gate]

_CAPABILITIES = [{"harness_id": "claude", "default": True}]
_FLOOR = {"lever": "floor", "detail": "exhausted", "subscription": "anthropic", "until": "2026-10-22T14:00:00Z"}
_PACE = {"lever": "pace", "detail": "7d 86% spent, 71% elapsed"}


class _PreFieldRegistration(BaseModel):
    """The registration body as a hub built before ``declining`` reads it — frozen here, field for field, so a change
    to the live model cannot hide a break in what an older hub parses. Pydantic's default ``extra`` ignores a key it
    does not name."""

    name: str | None = None
    workspace_id: str
    env_capacity: int | None = None
    url: str | None = None
    redirect_uris: list[str] = []
    capabilities: list[dict[str, Any]] = []
    subscriptions: list[dict[str, Any]] | None = None
    gates: list[str] = []
    projects: list[str] | None = None
    version: str | None = None


def _add(hub: httpx.Client, name: str) -> tuple[str, dict[str, str]]:
    added = hub.post("/api/runners", json={"name": name})
    assert added.status_code == 201, added.text
    return added.json()["runner_id"], {"Authorization": f"Bearer {added.json()['token']}"}


def _register(hub: httpx.Client, bearer: dict[str, str], **declared: Any) -> None:
    body = {"workspace_id": "w1", "capabilities": _CAPABILITIES, **declared}
    registered = hub.post("/api/fleet/runners", json=body, headers=bearer)
    assert registered.status_code == 201, registered.text


def _held(hub: httpx.Client, runner_id: str) -> list[dict[str, Any]]:
    """The reasons the operator registry shows ``runner_id`` declining on."""
    registry = hub.get(f"/api/runners/{runner_id}")
    assert registry.status_code == 200, registry.text
    return list(registry.json()["declining"])


def test_a_hub_shows_the_reasons_a_runner_reports_and_nothing_from_one_that_predates_the_field(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with run_forge(bin_dir, origins, forge_port), run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        runner_id, bearer = _add(hub, "older-runner")

        _register(hub, bearer)
        assert _held(hub, runner_id) == []

        _register(hub, bearer, declining=[_FLOOR, _PACE])
        assert [(d["lever"], d["subscription"], d["until"]) for d in _held(hub, runner_id)] == [
            ("floor", "anthropic", "2026-10-22T14:00:00+00:00"),
            ("pace", None, None),
        ]

        _register(hub, bearer, declining=[_PACE])
        assert [d["lever"] for d in _held(hub, runner_id)] == ["pace"]

        _register(hub, bearer)
        assert _held(hub, runner_id) == []


def test_a_hub_that_predates_the_field_parses_a_registration_carrying_it() -> None:
    body = {"workspace_id": "w1", "capabilities": _CAPABILITIES, "declining": [_FLOOR]}

    parsed = _PreFieldRegistration.model_validate(body)

    assert parsed.workspace_id == "w1"
    assert not hasattr(parsed, "declining")
