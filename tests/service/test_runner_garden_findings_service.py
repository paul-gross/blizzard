"""Runner service tier — the garden-findings proxy leg against a real mock hub.

The mock hub's own ``GET /api/fleet/chunks/{id}/garden/findings`` is what a real
runner's ``GET /api/leases/{id}/garden/findings`` proxies to, over a real process
boundary — a real ``blizzard-runner host`` subprocess, with no ``BZ_HUB_URL`` anywhere
near the worker call that reaches it. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import httpx2
import pytest

from tests.fleet_launchers import runner_api, runner_config
from tests.fleet_world import mint_world
from tests.service.runner_support import mint_lease, tick_env, worker_credential
from tests.service.support import (
    mock_hub,
    mock_hub_chunk_spec,
    require_mock_fleet,
    service_gate,
)
from tests.support import free_port

pytestmark = [pytest.mark.service, service_gate]

_WORK_REF_URL = "https://example.invalid/issues/1"


def _garden_chunk_spec(work_ref: str) -> dict:
    """``mock_hub_chunk_spec`` plus a seeded garden run and one live finding — the mock
    companion lever ``bzh:wire-change-extends-mock`` requires alongside the route."""
    spec = mock_hub_chunk_spec(work_ref)
    spec["garden_run"] = {"routine_name": "nightly", "scope_slug": "blizzard"}
    spec["garden_findings"] = [
        {"finding_id": "fin_1", "class": "stale-docstring", "locus": "a.py:1", "summary": "s"},
        {"finding_id": "fin_2", "class": "c", "locus": "a.py:2", "summary": "s", "live": False, "state": "resolved"},
    ]
    return spec


def test_a_workers_garden_findings_read_proxies_through_to_the_mock_hubs_bucket(tmp_path: Path) -> None:
    """A worker holding only a lease reads its live finding bucket through a real
    runner, hub-proxied to a real mock hub — no `BZ_HUB_URL`, no hub credential, in the
    worker's own call at all. The bucket excludes the seeded non-live finding."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_world(bin_dir, tmp_path / "scratch")
    fenced = tick_env()

    hub_port = free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/chunk", json=_garden_chunk_spec(_WORK_REF_URL))
        assert seeded.status_code == 201, seeded.text
        chunk_id = seeded.json()["chunk_id"]

        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, host="127.0.0.1", port=free_port())

        with runner_api(config):
            runner_client = httpx2.Client(base_url=config.local_api_url, timeout=10.0)
            try:
                lease_id = mint_lease(config, fenced, runner_client, chunk_id)
                worker = worker_credential(config, lease_id)

                # No further ticks: the loop only advances when this test calls it, so
                # the lease found above stays active for the read below.
                findings = runner_client.get(f"/api/leases/{lease_id}/garden/findings", headers=worker)
                assert findings.status_code == 200, findings.text
                rows = findings.json()
                assert [r["finding_id"] for r in rows] == ["fin_1"]
                assert rows[0]["routine_name"] == "nightly" and rows[0]["scope_slug"] == "blizzard"
            finally:
                runner_client.close()


def test_a_leases_chunk_with_no_run_context_is_a_legible_refusal_not_an_empty_list(tmp_path: Path) -> None:
    """A chunk that is not a routine run — no seeded ``garden_run`` — refuses the read
    rather than answering an empty bucket, forwarded verbatim from the mock hub."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_world(bin_dir, tmp_path / "scratch")
    fenced = tick_env()

    hub_port = free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/chunk", json=mock_hub_chunk_spec(_WORK_REF_URL))  # no garden_run at all
        assert seeded.status_code == 201, seeded.text
        chunk_id = seeded.json()["chunk_id"]

        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, host="127.0.0.1", port=free_port())

        with runner_api(config):
            runner_client = httpx2.Client(base_url=config.local_api_url, timeout=10.0)
            try:
                lease_id = mint_lease(config, fenced, runner_client, chunk_id)
                worker = worker_credential(config, lease_id)

                findings = runner_client.get(f"/api/leases/{lease_id}/garden/findings", headers=worker)
                assert findings.status_code == 404, findings.text
                assert "no run context" in findings.json()["detail"]
            finally:
                runner_client.close()
