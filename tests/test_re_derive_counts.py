"""A re-derive counts only the segments it actually derived, through the real service and
store: a segment named that no longer stores content derives nothing and is not counted."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.domain.observability.analytics.derivation import ReDeriveScope
from tests.support import build_hub
from tests.test_analytics_events_api import _ingest_chunk, _record, _tool_turn

pytestmark = pytest.mark.component


def _hub_with_one_segment(tmp_path: Path):  # type: ignore[no-untyped-def]
    hub = build_hub(tmp_path)
    chunk_id = _ingest_chunk(hub)
    turns = [_tool_turn(0, "Read", {"file_path": "src/a.py"}, timestamp="2026-08-12T09:00:00Z")]
    push = hub.client.post(
        "/api/fleet/transcripts", json={"runner_id": "r1", "records": [_record(chunk_id, turns=turns)]}
    )
    assert push.status_code == 200, push.text
    return hub, chunk_id


def test_a_vanished_segment_is_not_counted(tmp_path: Path) -> None:
    hub, _chunk_id = _hub_with_one_segment(tmp_path)

    outcome = hub.services.event_derivation_service.re_derive(
        ReDeriveScope.of(segment_id="sg_missing", chunk_id=None), limit=10
    )

    assert (outcome.derived, outcome.remaining) == (0, 0)


def test_a_stored_segment_is_counted(tmp_path: Path) -> None:
    hub, chunk_id = _hub_with_one_segment(tmp_path)

    by_segment = hub.services.event_derivation_service.re_derive(
        ReDeriveScope.of(segment_id="sg_1", chunk_id=None), limit=10
    )
    by_chunk = hub.services.event_derivation_service.re_derive(
        ReDeriveScope.of(segment_id=None, chunk_id=chunk_id), limit=10
    )

    assert by_segment.derived == 1
    assert by_chunk.derived == 0  # the forced derive left its marker current: no candidate remains
