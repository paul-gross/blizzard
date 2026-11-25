"""Live settings reach the services built by the hosted composition root."""

from pathlib import Path

from fastapi.testclient import TestClient

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.platform_tracing.handle import DisabledPlatformTracing
from blizzard.hub.auth.throttle import LiveIpThrottle
from blizzard.hub.composition import build_hub_settings, open_hub_store
from blizzard.hub.config_keys import HUB_REGISTRY
from blizzard.hub.settings.footing import HubSettingsFooting
from tests.support.config_inputs import FLAG_REGISTRY, build_hosted_hub_app, init_hub


def test_hosted_services_follow_committed_settings(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")
    tracing = DisabledPlatformTracing()
    engine, connections = open_hub_store(config.db_url, tracing)
    footing = HubSettingsFooting(config.root, None, None, False, FLAG_REGISTRY, False, HUB_REGISTRY, {})
    settings = build_hub_settings(connections, SystemClock(), footing)
    app = build_hosted_hub_app(
        config, settings=settings, engine=engine, connections=connections, platform_tracing=tracing
    )
    client = TestClient(app)
    try:
        assert isinstance(app.state.hub_process.resolver.get(LiveIpThrottle), LiveIpThrottle)
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
