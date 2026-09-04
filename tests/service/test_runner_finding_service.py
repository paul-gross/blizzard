"""Runner service tier — the finding proxy legs against a real mock hub (blizzard#397
Phase 2). The mock hub's own ``GET /api/fleet/chunks/{id}/findings`` and
``.../findings/{finding_id}`` are what a real runner's ``GET /api/leases/{id}/findings``
and ``.../findings/{finding_id}`` proxy to, over a real process boundary — a real
``blizzard-runner host`` subprocess, with no ``BZ_HUB_URL`` anywhere near the worker call
that reaches it. Run with ``BLIZZARD_SERVICE=1``, the
``tests/service/test_runner_garden_findings_service.py`` shape."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import httpx
import pytest

from blizzard.runner.config import RunnerConfig
from tests.e2e.test_acceptance_loop import _free_port, _runner_api, _runner_config
from tests.service.support import (
    mint_fixture,
    mock_hub,
    mock_hub_chunk_spec,
    poll_until,
    require_mock_fleet,
    require_winter_source,
    service_gate,
)
from tests.service.test_runner_service import _tick_env, _tick_then, _worker_credential

pytestmark = [pytest.mark.service, service_gate]

_WORK_REF_URL = "https://example.invalid/issues/1"


def _answered_chunk_spec(work_ref: str) -> dict:
    """``mock_hub_chunk_spec`` plus the seeded answered-findings lever
    (``bzh:wire-change-extends-mock``) the two new routes require alongside the mock."""
    spec = mock_hub_chunk_spec(work_ref)
    spec["garden_answered_findings"] = [
        {
            "finding_id": "fin_1",
            "routine_name": "nightly",
            "scope_slug": "blizzard",
            "class": "stale-docstring",
            "locus": "a.py:1",
            "summary": "s",
        },
    ]
    return spec


def _mint_lease(config: RunnerConfig, fenced: dict[str, str], runner_client: httpx.Client, chunk_id: str) -> str:
    def _lease_minted() -> bool:
        items = runner_client.get("/api/leases").json()["items"]
        return any(item["chunk_id"] == chunk_id for item in items)

    minted = poll_until(lambda: _tick_then(config, fenced, _lease_minted), timeout=60.0)
    assert minted, "the chunk's first lease never minted"
    items = runner_client.get("/api/leases").json()["items"]
    return next(item["lease_id"] for item in items if item["chunk_id"] == chunk_id)


def test_a_workers_finding_reads_proxy_through_to_the_mock_hubs_answered_set(tmp_path: Path) -> None:
    """A worker holding only a lease reads its chunk's own answered findings — list and
    one get — through a real runner, hub-proxied to a real mock hub; no `BZ_HUB_URL`, no
    hub credential, in the worker's own call at all."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/chunk", json=_answered_chunk_spec(_WORK_REF_URL))
        assert seeded.status_code == 201, seeded.text
        chunk_id = seeded.json()["chunk_id"]

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, host="127.0.0.1", port=_free_port())

        with _runner_api(config):
            runner_client = httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=10.0)
            try:
                lease_id = _mint_lease(config, fenced, runner_client, chunk_id)
                worker = _worker_credential(config, lease_id)

                findings = runner_client.get(f"/api/leases/{lease_id}/findings", headers=worker)
                assert findings.status_code == 200, findings.text
                rows = findings.json()
                assert [r["finding_id"] for r in rows] == ["fin_1"]

                one = runner_client.get(f"/api/leases/{lease_id}/findings/fin_1", headers=worker)
                assert one.status_code == 200, one.text
                assert one.json()["finding_id"] == "fin_1"
            finally:
                runner_client.close()


def test_a_leases_chunk_answering_no_proposal_is_a_legible_refusal_not_an_empty_list(tmp_path: Path) -> None:
    """A chunk answering no accepted, minted garden proposal — no seeded
    ``garden_answered_findings`` — refuses the read rather than answering an empty list."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/chunk", json=mock_hub_chunk_spec(_WORK_REF_URL))  # no answered-findings at all
        assert seeded.status_code == 201, seeded.text
        chunk_id = seeded.json()["chunk_id"]

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, host="127.0.0.1", port=_free_port())

        with _runner_api(config):
            runner_client = httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=10.0)
            try:
                lease_id = _mint_lease(config, fenced, runner_client, chunk_id)
                worker = _worker_credential(config, lease_id)

                findings = runner_client.get(f"/api/leases/{lease_id}/findings", headers=worker)
                assert findings.status_code == 404, findings.text
                assert "no accepted, minted garden proposal" in findings.json()["detail"]
            finally:
                runner_client.close()
