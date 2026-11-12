"""A hub-wide cap edit reaches each already-open tenant on its next ingest."""

from pathlib import Path

from blizzard.foundation.flags import FlagRegistry
from blizzard.foundation.settings_record.vocabulary import Change
from blizzard.hub.api.auth_session import TENANT_HEADER
from blizzard.hub.composition import build_hub_settings
from blizzard.hub.config_keys import HUB_REGISTRY
from blizzard.hub.settings.footing import HubSettingsFooting
from tests.support.scaffolding import build_hub, default_tenant, hub_level_connections


def test_cap_edit_reaches_every_open_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    second = hub.add_tenant("bravo")
    tenants = (default_tenant(hub.engine), second.tenant)
    footing = HubSettingsFooting(tmp_path, None, None, False, FlagRegistry(), False, HUB_REGISTRY, {})
    settings = build_hub_settings(hub_level_connections(hub.engine), hub.clock, footing)
    hub.wired_app.state.hub_settings = settings
    chunks: list[str] = []
    for tenant in tenants:
        response = hub.client.post("/api/chunks", json={"tokens": ["hub:1"]}, headers={TENANT_HEADER: tenant})
        assert response.status_code == 201, response.text
        chunks.append(response.json()["chunk_id"])

    def ingest(tenant: str, chunk: str, seq: int):
        client = hub.client if tenant == tenants[0] else second.client
        runner_id = "r1" if tenant == tenants[0] else "bravo-r1"
        return client.post(
            "/api/fleet/transcripts",
            headers={TENANT_HEADER: tenant},
            json={
                "runner_id": runner_id,
                "records": [
                    {
                        "seq": seq,
                        "segment_id": "sg_1",
                        "chunk_id": chunk,
                        "node_id": "nd_build",
                        "epoch": 1,
                        "spawn_generation": 1,
                        "turn_range_start": seq,
                        "turn_range_end": seq,
                        "final": False,
                        "normalizer_version": "v1",
                        "harness_version": "v1",
                        "turns": [
                            {
                                "index": seq,
                                "kind": "asst",
                                "text": "a transcript turn",
                                "timestamp": None,
                                "tool": None,
                                "thinking_redacted": False,
                                "sidechain": None,
                                "truncated": False,
                            }
                        ],
                    }
                ],
            },
        )

    for tenant, chunk in zip(tenants, chunks, strict=True):
        response = ingest(tenant, chunk, 1)
        assert response.status_code == 200, response.text
        assert response.json()["applied"] == [1]
    settings.authoring.apply(
        Change("edit", sets={"transcripts.record_max_bytes": 1}), if_match=0, door="api", actor="test"
    )
    for tenant, chunk in zip(tenants, chunks, strict=True):
        response = ingest(tenant, chunk, 2)
        assert response.status_code == 200, response.text
        assert response.json()["capped"] == [2]
