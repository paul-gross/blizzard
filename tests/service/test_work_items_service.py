"""The runner's work-items proxy against a real mock hub, over a real HTTP hop
(blizzard#362) — the wire-change-extends-mock companion landing owes this: this route
forwards through the generic ``HubProxy``, not ``IHubClient``, so
``tests/service/test_parity_guard.py``'s mechanical diff never enumerates it."""

from __future__ import annotations

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
