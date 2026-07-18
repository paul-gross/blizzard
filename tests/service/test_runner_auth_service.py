"""Runner presents its bearer token on every hub call — service tier (issue #86b).

Phase 1 (#86a) landed the hub-side check, warn-only, at registration. This phase makes
the **runner** the presenting side: every outbound ``httpx.Client`` construction
(``build.py``) and the pm-items proxy fold in the same ``Authorization: Bearer`` header
built from :meth:`~blizzard.runner.config.RunnerConfig.auth_headers`. The header-inspection
lever (``blizzard-mock`` ``GET /_captured``, issue #86b) makes that assertable against a
**real** mock-hub subprocess rather than a stub: every fleet-facing ``/api/*`` request it
receives is logged with its headers, in arrival order.

Two scenarios:

* a runner configured with a token carries it on every call — registration, queue peek,
  the claim/completion path that lands a chunk, and the pm-items proxy forward — asserted
  against the mock hub's captured log;
* a runner configured with **no** token (unenrolled — the still-supported warn-mode
  default) sends no ``Authorization`` header at all, and the hub (which stays ``warn``
  by default) still serves it.

The test's *own* status polling reaches the same ``GET /api/chunks/{id}`` path the real
runner also calls (``HttpHubClient.get_chunk``), so both would otherwise land in the
capture indistinguishably; the polling client marks its own calls with a probe header
(``X-Test-Probe``) so the assertion below can filter its own noise out and check only what
the runner itself sent.

Reproduce — from a provisioned feature env — with::

    BLIZZARD_SERVICE=1 uv run pytest tests/service/test_runner_auth_service.py
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import httpx
import pytest

from blizzard.runner.config import RunnerConfig
from tests.e2e.test_acceptance_loop import _free_port, _runner_api, _runner_config
from tests.service.support import (
    mint_fixture,
    mock_hub,
    poll_until,
    require_mock_fleet,
    require_winter_source,
    service_gate,
)
from tests.service.test_runner_service import _drive, _seed, _tick_env

pytestmark = [pytest.mark.service, service_gate]

_TOKEN = "svc-auth-token"
_PROBE_HEADER = "X-Test-Probe"


def _captured_from_the_runner(hub: httpx.Client) -> list[dict[str, Any]]:
    """The capture log, with the test's own probed status-poll calls filtered out."""
    resp = hub.get("/_captured")
    assert resp.status_code == 200, resp.text
    return [e for e in resp.json()["requests"] if _PROBE_HEADER.lower() not in e["headers"]]


def _status(hub: httpx.Client, chunk_id: str) -> str:
    """The test's own out-of-band status read, marked so it never masquerades as a runner
    call. The mock hub's whole hub mirror moved under ``/api/fleet`` (issue #87)."""
    resp = hub.get(f"/api/fleet/chunks/{chunk_id}", headers={_PROBE_HEADER: "1"})
    return resp.json()["status"]


def test_runner_presents_the_bearer_token_on_every_hub_call(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        chunk_id = _seed(hub)
        base_config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(base_config, hub_token=_TOKEN)

        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=90.0)
        assert landed, f"chunk did not land (status {_status(hub, chunk_id)!r})"

        requests = _captured_from_the_runner(hub)
        assert requests, "the mock hub captured no runner requests at all"
        for entry in requests:
            headers = entry["headers"]
            assert headers.get("authorization") == f"Bearer {_TOKEN}", (
                f"{entry['method']} {entry['path']} carried no/wrong Authorization header: {headers}"
            )
        # Registration and the claim/completion path both rode the same client.
        paths = {entry["path"] for entry in requests}
        assert "/api/fleet/runners" in paths
        assert f"/api/fleet/chunks/{chunk_id}/completions" in paths

        # The pm-items proxy path — a separately-constructed httpx call in
        # `runner/api/pm_items.py` — carries the same credential, not a patched one.
        api_config = dataclasses.replace(config, host="127.0.0.1", port=_free_port())
        with _runner_api(api_config):
            runner_client = httpx.Client(base_url=f"http://{api_config.host}:{api_config.port}", timeout=10.0)
            try:
                runner_client.get(f"/api/chunks/{chunk_id}/pm-items")
            finally:
                runner_client.close()

        pm_items_calls = [
            e for e in _captured_from_the_runner(hub) if e["path"] == f"/api/fleet/chunks/{chunk_id}/pm-items"
        ]
        assert pm_items_calls, "the pm-items proxy never reached the mock hub"
        assert pm_items_calls[-1]["headers"].get("authorization") == f"Bearer {_TOKEN}"


def test_runner_with_no_token_sends_no_authorization_header(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BZ_HUB_TOKEN", raising=False)
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        assert config.hub_token == "", "the runner config must scaffold with no token for this scenario"

        # A couple of ticks is enough to register + heartbeat + peek — no chunk needed.
        _drive(config, fenced, ticks=2, pause=0.3)

        requests = _captured_from_the_runner(hub)
        assert requests, "the mock hub captured no runner requests at all"
        assert all("authorization" not in entry["headers"] for entry in requests), (
            f"an unenrolled runner sent an Authorization header: {requests}"
        )


def _run_and_check(config: RunnerConfig, fenced: dict[str, str], hub: httpx.Client, chunk_id: str, target: str) -> bool:
    _drive(config, fenced, ticks=1, pause=0.3)
    return _status(hub, chunk_id) == target
