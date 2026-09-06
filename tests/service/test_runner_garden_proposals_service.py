"""Runner service tier — the garden-proposals proxy leg against a real mock hub.

The mock hub's own ``GET /api/fleet/chunks/{id}/garden/proposals`` is what a real
runner's ``GET /api/leases/{id}/garden/proposals`` proxies to, over a real process
boundary — a real ``blizzard-runner host`` subprocess, with no ``BZ_HUB_URL`` anywhere
near the worker call that reaches it. Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import httpx
import pytest

from tests.e2e.test_acceptance_loop import _free_port, _runner_api, _runner_config
from tests.service.support import (
    mint_fixture,
    mock_hub,
    mock_hub_chunk_spec,
    require_mock_fleet,
    require_winter_source,
    service_gate,
)
from tests.service.test_runner_garden_findings_service import _mint_lease
from tests.service.test_runner_service import _tick_env, _worker_credential

pytestmark = [pytest.mark.service, service_gate]

_WORK_REF_URL = "https://example.invalid/issues/1"


def _garden_proposals_chunk_spec(work_ref: str) -> dict:
    """``mock_hub_chunk_spec`` plus a seeded garden run and one open proposal — the mock
    companion lever ``bzh:wire-change-extends-mock`` requires alongside the route."""
    spec = mock_hub_chunk_spec(work_ref)
    spec["garden_run"] = {"routine_name": "nightly", "scope_slug": "blizzard"}
    spec["garden_proposals"] = [
        {"proposal_id": "prop_1", "class": "mechanize", "title": "t", "body": "b", "findings": ["fin_1"]},
    ]
    return spec


def test_a_workers_garden_proposals_read_proxies_through_to_the_mock_hubs_bucket(tmp_path: Path) -> None:
    """A worker holding only a lease reads its routine's own open proposal docket
    through a real runner, hub-proxied to a real mock hub — no `BZ_HUB_URL`, no hub
    credential, in the worker's own call at all."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/chunk", json=_garden_proposals_chunk_spec(_WORK_REF_URL))
        assert seeded.status_code == 201, seeded.text
        chunk_id = seeded.json()["chunk_id"]

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, host="127.0.0.1", port=_free_port())

        with _runner_api(config):
            runner_client = httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=10.0)
            try:
                lease_id = _mint_lease(config, fenced, runner_client, chunk_id)
                worker = _worker_credential(config, lease_id)

                # No further ticks: the loop only advances when this test calls it, so
                # the lease found above stays active for the read below.
                proposals = runner_client.get(f"/api/leases/{lease_id}/garden/proposals", headers=worker)
                assert proposals.status_code == 200, proposals.text
                rows = proposals.json()
                assert [r["proposal_id"] for r in rows] == ["prop_1"]
                assert rows[0]["routine_name"] == "nightly"
                assert rows[0]["findings"] == ["fin_1"]
            finally:
                runner_client.close()


def test_a_leases_chunk_with_no_run_context_is_a_legible_refusal_not_an_empty_list(tmp_path: Path) -> None:
    """A chunk that is not a routine run — no seeded ``garden_run`` — refuses the read
    rather than answering an empty docket, forwarded verbatim from the mock hub."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/chunk", json=mock_hub_chunk_spec(_WORK_REF_URL))  # no garden_run at all
        assert seeded.status_code == 201, seeded.text
        chunk_id = seeded.json()["chunk_id"]

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, host="127.0.0.1", port=_free_port())

        with _runner_api(config):
            runner_client = httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=10.0)
            try:
                lease_id = _mint_lease(config, fenced, runner_client, chunk_id)
                worker = _worker_credential(config, lease_id)

                proposals = runner_client.get(f"/api/leases/{lease_id}/garden/proposals", headers=worker)
                assert proposals.status_code == 404, proposals.text
                assert "no run context" in proposals.json()["detail"]
            finally:
                runner_client.close()
