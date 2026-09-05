"""The runner's work-items proxy against a real mock hub, over a real HTTP hop
(blizzard#362) — the wire-change-extends-mock companion landing owes this: this route
forwards through the generic ``HubProxy``, not ``IHubClient``, so
``tests/service/test_parity_guard.py``'s mechanical diff never enumerates it."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import httpx
import pytest

from tests.e2e.test_acceptance_loop import REPO, _free_port, _runner_api, _runner_config
from tests.service.support import mock_hub, mock_hub_chunk_spec, require_mock_fleet, require_winter_source, service_gate

pytestmark = [pytest.mark.service, service_gate]

_WORK_REF_URL = f"{REPO}/issues/1"


def test_work_items_proxy_carries_the_widened_fields_unchanged_over_a_real_hop(tmp_path: Path) -> None:
    """The hub's ``WorkItemEntry`` — now carrying ``author``/``stated_priority`` — is
    re-validated through the runner's own ``WorkItemsView`` model on the proxy hop; a
    field the hop dropped or defaulted differently would fail this."""
    bin_dir = require_mock_fleet()
    require_winter_source()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/chunk", json=mock_hub_chunk_spec(_WORK_REF_URL))
        assert seeded.status_code == 201, seeded.text
        chunk_id = seeded.json()["chunk_id"]

        config = _runner_config(tmp_path / "runner", tmp_path / "workspace", bin_dir, hub_port)

        with _runner_api(config):
            client = httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=10.0)
            try:
                resp = client.get(f"/api/chunks/{chunk_id}/work-items")
            finally:
                client.close()

        assert resp.status_code == 200, resp.text
        items = resp.json()["items"]
        assert len(items) == 1
        entry = items[0]
        assert entry["source"] == "mock"
        # The mock's canned read has no author/priority lever — this proves the widened
        # keys survive the hop unchanged, not that the mock fills them non-null.
        assert "author" in entry
        assert "stated_priority" in entry
        assert entry["author"] is None
        assert entry["stated_priority"] is None


def test_a_worker_lane_proxied_read_rides_out_a_real_mock_hub_restart(tmp_path: Path) -> None:
    """``HubProxy.forward``'s retry (blizzard#467) over a real bounce: the mock hub is
    killed, the runner's proxied read is already in flight against the closed port, and
    the hub comes back up on the same port before the runner's retry budget runs out —
    the read still answers ``200``, never surfacing the restart window's `502` to the
    worker."""
    bin_dir = require_mock_fleet()
    require_winter_source()

    # A fixed id: the mock hub's state is in-memory and per-process, so the respawned
    # instance below needs the same chunk re-seeded onto it under the id the runner
    # already has in flight, not whatever id a fresh random mint would pick.
    chunk_id = "ch_restart_test"
    chunk_spec = {**mock_hub_chunk_spec(_WORK_REF_URL), "chunk_id": chunk_id}

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/chunk", json=chunk_spec)
        assert seeded.status_code == 201, seeded.text
        assert seeded.json()["chunk_id"] == chunk_id

    # The mock hub is down now (its process was terminated on the `with` block's exit) —
    # the runner started below points at its now-unoccupied port.
    config = _runner_config(tmp_path / "runner", tmp_path / "workspace", bin_dir, hub_port)

    respawned = threading.Event()

    def respawn_after_a_beat() -> None:
        time.sleep(1.5)
        with mock_hub(bin_dir, hub_port) as respawned_hub:
            respawned_hub.post("/_seed/chunk", json=chunk_spec)
            respawned.set()
            time.sleep(3.0)  # stay up long enough for the in-flight read to land

    respawn_thread = threading.Thread(target=respawn_after_a_beat, daemon=True)

    with _runner_api(config):
        respawn_thread.start()
        client = httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=40.0)
        try:
            resp = client.get(f"/api/chunks/{chunk_id}/work-items")
        finally:
            client.close()
        respawn_thread.join(timeout=10.0)

    assert respawned.is_set()  # the scenario actually exercised a real down-then-up bounce
    assert resp.status_code == 200, resp.text
    assert resp.json()["items"][0]["source"] == "mock"
