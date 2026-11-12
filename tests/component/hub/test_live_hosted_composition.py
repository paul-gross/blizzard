"""Live settings reach the services built by the hosted composition root."""

from pathlib import Path

from fastapi.testclient import TestClient

from blizzard.foundation.clock import SystemClock
from blizzard.hub.app import open_hub_engine
from blizzard.hub.auth.throttle import LiveIpThrottle
from blizzard.hub.composition import build_hub_settings
from blizzard.hub.config_keys import HUB_REGISTRY
from blizzard.hub.settings.footing import HubSettingsFooting
from tests.support.config_inputs import FLAG_REGISTRY, build_hosted_hub_app, init_hub
from tests.support.scaffolding import hub_level_connections


def test_hosted_services_follow_committed_settings(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")
    engine = open_hub_engine(config.db_url)
    footing = HubSettingsFooting(config.root, None, None, False, FLAG_REGISTRY, False, HUB_REGISTRY, {})
    settings = build_hub_settings(hub_level_connections(engine), SystemClock(), footing)
    app = build_hosted_hub_app(config, settings=settings)
    client = TestClient(app)
    try:
        assert isinstance(app.state.services.auth_throttle, LiveIpThrottle)
        response = client.post(
            "/api/admin/settings/apply",
            json={"tracing": {"replay_max_window": 123}, "egress": {"backfill_max_window": 456}},
        )
        assert response.status_code == 200, response.text
        assert client.get("/api/traces/status").json()["replay_max_window_seconds"] == 123
        assert client.get("/api/egress/status").json()["backfill_max_window_seconds"] == 456
    finally:
        app.state.hosted_cleanup.close()
        engine.dispose()
