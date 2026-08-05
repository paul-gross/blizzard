"""Runner presents its bearer token on every hub call — service tier (issue #86b).

Every outbound ``httpx.Client`` and the work-items proxy fold in the same
``Authorization: Bearer`` header, assertable against a real mock-hub subprocess via
``GET /_captured``. Covers a token-bearing runner (every call carries it) and an
unenrolled runner (no header, hub still serves it in warn mode)."""

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
from tests.service.test_runner_service import _WORK_REF_URL, _drive, _seed, _tick_env

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
    call."""
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

        # The work-items proxy path — a separately-constructed httpx call in
        # `runner/api/work_items.py` — carries the same credential, not a patched one.
        api_config = dataclasses.replace(config, host="127.0.0.1", port=_free_port())
        with _runner_api(api_config):
            runner_client = httpx.Client(base_url=f"http://{api_config.host}:{api_config.port}", timeout=10.0)
            try:
                proxied = runner_client.get(f"/api/chunks/{chunk_id}/work-items")
            finally:
                runner_client.close()
        # Assert the payload too, not just status: a capture-only check passed green
        # while the mock hub 404'd the forwarded call (issue #55).
        assert proxied.status_code == 200, f"the proxy forward failed upstream: {proxied.status_code} {proxied.text}"
        proxied_items = proxied.json()["items"]
        assert [i["ref"] for i in proxied_items] == [_WORK_REF_URL], (
            f"the seeded work ref did not survive seed -> mock hub -> proxy: {proxied_items}"
        )

        work_items_calls = [
            e for e in _captured_from_the_runner(hub) if e["path"] == f"/api/fleet/chunks/{chunk_id}/work-items"
        ]
        assert work_items_calls, "the work-items proxy never reached the mock hub"
        assert work_items_calls[-1]["headers"].get("authorization") == f"Bearer {_TOKEN}"


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
