"""Dashboard route service tier — the real runner against a real mock hub (blizzard#311).

The component tier (`tests/test_dashboard_route.py`) proves this route in-process, against
a monkeypatched `httpx.request`. This proves it over a genuinely running daemon: a real
`uvicorn`-served runner and a real mock-hub *process*, reached over actual TCP."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.domain.leases import NewLease
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from tests.e2e.test_acceptance_loop import _free_port, _runner_api, _runner_config
from tests.runner_fakes import runner_store_errors
from tests.service.support import mint_fixture, mock_hub, require_mock_fleet, require_winter_source, service_gate

pytestmark = [pytest.mark.service, service_gate]

_NOW = datetime.now(UTC)


def _seed_all_local_sections(store: SqlAlchemyRunnerStore) -> None:
    """Real facts behind each of the six local sections — the same shape
    `tests/test_dashboard_route.py`'s component test seeds, against a real sqlite store."""
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="runner-local",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_ask(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="which branch?",
        options=["main", "dev"],
        session_id="sess-a",
        asked_at=_NOW,
    )
    store.enqueue_outbound(kind="lease.minted", chunk_id="ch_1", lease_id="lease_1", payload="{}", created_at=_NOW)
    store.record_takeover(
        takeover_id="tko_1",
        chunk_id="ch_1",
        lease_id=None,
        session_id="sess-a",
        workdir="/ws/e1",
        fence_epoch=None,
        opened_at=_NOW,
    )
    # A second, escalated chunk/lease — so the escalations section has real data too.
    store.record_lease(
        NewLease(
            lease_id="lease_2",
            chunk_id="ch_2",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="runner-local",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn("lease_2", pid=200, process_start_time="start-200", session_id="sess-b", spawned_at=_NOW)
    store.record_binding(chunk_id="ch_2", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    store.record_closure(
        lease_id="lease_2",
        chunk_id="ch_2",
        node_id="nd_build",
        reason="escalated",
        closed_at=_NOW + timedelta(minutes=5),
    )


def test_dashboard_matches_the_individual_reads_over_a_real_running_daemon(tmp_path: Path) -> None:
    """The composed route's six local sections, read over genuine TCP, match what each
    individual route answers over the same connection. The mock hub carries no
    `/api/fleet/summary` route, so `fleet_summary` degrades to `null` for real."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    hub_port = _free_port()

    with mock_hub(bin_dir, hub_port) as hub:
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        engine = create_engine_from_url(config.db_url)
        try:
            _seed_all_local_sections(SqlAlchemyRunnerStore(engine, runner_store_errors()))
        finally:
            engine.dispose()

        with _runner_api(config):
            client = httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=10.0)
            try:
                dashboard = client.get("/api/dashboard")
                assert dashboard.status_code == 200, dashboard.text
                body = dashboard.json()

                runner = client.get("/api/runner")
                environments = client.get("/api/environments")
                asks = client.get("/api/asks", params={"open": True})
                escalations = client.get("/api/escalations")
                takeovers = client.get("/api/takeovers")
                facts = client.get("/api/facts")
                for resp in (runner, environments, asks, escalations, takeovers, facts):
                    assert resp.status_code == 200, resp.text

                assert body["runner"] == runner.json()
                assert body["environments"] == environments.json()
                assert body["asks"] == asks.json()
                assert body["escalations"] == escalations.json()
                assert body["takeovers"] == takeovers.json()
                assert body["facts"] == facts.json()

                # Sanity: this proves real, non-empty data round-tripped — not two empty
                # bodies trivially equal to each other.
                assert body["environments"]["items"], "no environments came back for real"
                assert body["asks"]["items"], "no asks came back for real"
                assert body["escalations"]["items"], "no escalations came back for real"
                assert body["takeovers"]["items"], "no takeovers came back for real"
                assert body["facts"]["items"], "no facts came back for real"

                # The mock hub answers no `/api/fleet/summary` route (404) — the composed
                # route degrades that one slot to `null` rather than failing the whole read.
                assert body["fleet_summary"] is None, body["fleet_summary"]
            finally:
                client.close()

            # A genuine hub outage (503 via the mock's own lever) — a second, distinct
            # real-HTTP failure mode than the "route not implemented" case above.
            assert hub.post("/_levers/unreachable", json={"remaining": 10_000}).status_code == 200
            client2 = httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=10.0)
            try:
                degraded = client2.get("/api/dashboard")
                assert degraded.status_code == 200, degraded.text
                degraded_body = degraded.json()
                assert degraded_body["fleet_summary"] is None
                assert degraded_body["environments"]["items"], "local sections must stay populated on a hub outage"
                assert degraded_body["asks"]["items"]
            finally:
                client2.close()
