"""Runner service tier — the analytics proxy legs against a real mock hub (blizzard#545).

The mock hub's own ``GET /api/fleet/chunks/{id}/analytics/...`` is what a real runner's
``GET /api/leases/{id}/analytics/...`` proxies to, over a real process boundary — a real
``blizzard-runner host`` subprocess, with no ``BZ_HUB_URL`` anywhere near the worker call
that reaches it. Run with ``BLIZZARD_SERVICE=1``."""

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


def _analytics_chunk_spec(work_ref: str) -> dict:
    """``mock_hub_chunk_spec`` plus a seeded garden run and one seeded counts/spend
    row each — the mock companion lever ``bzh:wire-change-extends-mock`` requires
    alongside the route."""
    spec = mock_hub_chunk_spec(work_ref)
    spec["garden_run"] = {"routine_name": "nightly", "scope_slug": "blizzard"}
    spec["analytics"] = {
        "counts_skills": [{"key": "wf-commit", "count": 2}],
        "spend_nodes": [
            {
                "key": "nd_build",
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_tokens": 10,
                "cache_create_tokens": 5,
                "cost_usd": 0.1,
                "cost_partial": False,
            }
        ],
    }
    return spec


def _mint_lease(config: RunnerConfig, fenced: dict[str, str], runner_client: httpx.Client, chunk_id: str) -> str:
    """Drive ticks until the chunk's first lease mints, returning its own lease id."""

    def _lease_minted() -> bool:
        items = runner_client.get("/api/leases").json()["items"]
        return any(item["chunk_id"] == chunk_id for item in items)

    minted = poll_until(lambda: _tick_then(config, fenced, _lease_minted), timeout=60.0)
    assert minted, "the chunk's first lease never minted"
    items = runner_client.get("/api/leases").json()["items"]
    return next(item["lease_id"] for item in items if item["chunk_id"] == chunk_id)


def test_a_workers_analytics_reads_proxy_through_to_the_mock_hubs_seeded_rows(tmp_path: Path) -> None:
    """A worker holding only a lease reads its counts/spend rows through a real runner,
    hub-proxied to a real mock hub — no ``BZ_HUB_URL``, no hub credential, in the
    worker's own call at all."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/chunk", json=_analytics_chunk_spec(_WORK_REF_URL))
        assert seeded.status_code == 201, seeded.text
        chunk_id = seeded.json()["chunk_id"]

        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, host="127.0.0.1", port=_free_port())

        with _runner_api(config):
            runner_client = httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=10.0)
            try:
                lease_id = _mint_lease(config, fenced, runner_client, chunk_id)
                worker = _worker_credential(config, lease_id)

                counts = runner_client.get(
                    f"/api/leases/{lease_id}/analytics/counts/skills",
                    params={"since": "2020-01-01T00:00:00Z"},
                    headers=worker,
                )
                assert counts.status_code == 200, counts.text
                assert counts.json()["counts"] == [{"key": "wf-commit", "count": 2}]

                spend = runner_client.get(
                    f"/api/leases/{lease_id}/analytics/spend/nodes",
                    params={"since": "2020-01-01T00:00:00Z"},
                    headers=worker,
                )
                assert spend.status_code == 200, spend.text
                assert spend.json()["spend"][0]["key"] == "nd_build"
            finally:
                runner_client.close()


def test_a_leases_chunk_with_no_run_context_is_a_legible_refusal_not_an_empty_bucket(tmp_path: Path) -> None:
    """A chunk that is not a routine run — no seeded ``garden_run`` — refuses the read
    rather than answering an empty bucket, forwarded verbatim from the mock hub."""
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

                resp = runner_client.get(
                    f"/api/leases/{lease_id}/analytics/counts/files",
                    params={"since": "2020-01-01T00:00:00Z"},
                    headers=worker,
                )
                assert resp.status_code == 404, resp.text
                assert "no run context" in resp.json()["detail"]
            finally:
                runner_client.close()
