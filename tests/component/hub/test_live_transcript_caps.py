"""A fleet ingest holds its request's caps while later requests see the edit."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from blizzard.foundation.flags import FlagRegistry
from blizzard.foundation.settings_record.vocabulary import Change
from blizzard.hub.composition import build_hub_settings
from blizzard.hub.config_keys import HUB_REGISTRY
from blizzard.hub.domain.observability.transcripts import TranscriptCaps, TranscriptIngestResult, TranscriptSlice
from blizzard.hub.settings.footing import HubSettingsFooting
from tests.support.hub_request_auth import ingest_chunk
from tests.support.scaffolding import build_hub, hub_level_connections


def test_committed_caps_reach_next_ingest_but_not_in_flight_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    footing = HubSettingsFooting(tmp_path, None, None, False, FlagRegistry(), False, HUB_REGISTRY, {})
    settings = build_hub_settings(hub_level_connections(hub.engine), hub.clock, footing)
    hub.app.state.hub_settings = settings
    chunk = ingest_chunk(hub)
    service = hub.services.transcript_ingest
    ingest = service.ingest
    entered, release = Event(), Event()

    def paused(
        runner_id: str, records: list[tuple[int, TranscriptSlice]], *, caps: TranscriptCaps | None = None
    ) -> TranscriptIngestResult:
        entered.set()
        assert release.wait(5)
        return ingest(runner_id, records, caps=caps)

    monkeypatch.setattr(service, "ingest", paused)

    def payload(seq: int) -> dict[str, Any]:
        return {
            "runner_id": "r1",
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
        }

    with ThreadPoolExecutor() as pool:
        future = pool.submit(hub.client.post, "/api/fleet/transcripts", json=payload(1))
        try:
            assert entered.wait(5), future.result() if future.done() else "request did not reach ingest"
            settings.authoring.apply(
                Change("edit", sets={"transcripts.record_max_bytes": 1}), if_match=0, door="api", actor="test"
            )
        finally:
            release.set()
        first = future.result(timeout=5)
    assert first.status_code == 200, first.text
    assert first.json()["applied"] == [1]
    second = hub.client.post("/api/fleet/transcripts", json=payload(2))
    assert second.status_code == 200, second.text
    assert second.json()["capped"] == [2]
