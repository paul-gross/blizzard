"""A re-derive counts only the segments it actually derived, through the real service and
store: a segment named that no longer stores content derives nothing and is not counted, and a
segment-scoped force refuses a segment the sweep does not count as visible."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from blizzard.hub.domain.observability.analytics.derivation import FORCED_FULL_PASS_FLOOR, ReDeriveScope
from blizzard.hub.store import schema as s
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


# --- a segment force overrides candidacy, not visibility ---------------------------------------


def _push(hub, chunk_id: str, *, segment_id: str, supersedes: str | None = None, final: bool = True) -> None:  # type: ignore[no-untyped-def]
    turns = [_tool_turn(0, "Read", {"file_path": "src/a.py"}, timestamp="2026-08-12T09:00:00Z")]
    record = _record(chunk_id, turns=turns, segment_id=segment_id)
    record["supersedes"] = supersedes
    record["final"] = final
    push = hub.client.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": [record]})
    assert push.status_code == 200, push.text


def _count(hub, table) -> int:  # type: ignore[no-untyped-def]
    with hub.engine.connect() as conn:
        return int(conn.execute(select(func.count()).select_from(table)).scalar_one())


def _force(hub, segment_id: str) -> dict:  # type: ignore[no-untyped-def]
    resp = hub.client.post("/api/analytics/re-derive", json={"segment_id": segment_id})
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_superseded_segment_is_refused_with_no_events_and_no_marker(tmp_path: Path) -> None:
    hub, chunk_id = _hub_with_one_segment(tmp_path)
    _push(hub, chunk_id, segment_id="sg_2", supersedes="sg_1")

    outcome = hub.services.event_derivation_service.re_derive(ReDeriveScope(segment_id="sg_1"), limit=10)

    assert (outcome.derived, outcome.remaining, outcome.not_visible) == (0, 0, True)
    assert _count(hub, s.transcript_event_derivations) == 0
    assert _count(hub, s.transcript_events) == 0


def test_a_non_final_segment_is_refused_with_no_events_and_no_marker(tmp_path: Path) -> None:
    hub, chunk_id = _hub_with_one_segment(tmp_path)
    _push(hub, chunk_id, segment_id="sg_open", final=False)

    outcome = hub.services.event_derivation_service.re_derive(ReDeriveScope(segment_id="sg_open"), limit=10)

    assert (outcome.derived, outcome.not_visible) == (0, True)
    assert _count(hub, s.transcript_event_derivations) == 0
    assert _count(hub, s.transcript_events) == 0


def test_a_visible_segment_is_still_forced(tmp_path: Path) -> None:
    hub, _chunk_id = _hub_with_one_segment(tmp_path)

    body = _force(hub, "sg_1")

    assert body == {"derived": 1, "remaining": 0, "not_visible": False}


def test_the_route_reports_a_not_visible_segment(tmp_path: Path) -> None:
    hub, chunk_id = _hub_with_one_segment(tmp_path)
    _push(hub, chunk_id, segment_id="sg_2", supersedes="sg_1")

    assert _force(hub, "sg_1") == {"derived": 0, "remaining": 0, "not_visible": True}
    assert _force(hub, "sg_missing") == {"derived": 0, "remaining": 0, "not_visible": True}


def test_a_sweep_after_a_refused_force_appends_no_drop_fact(tmp_path: Path) -> None:
    hub, chunk_id = _hub_with_one_segment(tmp_path)
    hub.services.event_derivation.sweep()  # sg_1 derived
    _push(hub, chunk_id, segment_id="sg_2", supersedes="sg_1")
    hub.services.event_derivation.sweep()  # sg_1 no longer visible: dropped once
    assert _count(hub, s.transcript_event_drops) == 1

    assert _force(hub, "sg_1")["not_visible"] is True
    hub.clock.advance(FORCED_FULL_PASS_FLOOR + timedelta(seconds=1))  # the next sweep runs, not skipped
    hub.services.event_derivation.sweep()

    assert _count(hub, s.transcript_event_drops) == 1


# --- a scope re-derive excludes a candidate whose graph pin does not resolve --------------------


def test_a_scope_re_derive_excludes_an_unresolvable_pin_candidate_from_derived(tmp_path: Path) -> None:
    hub, _chunk_id = _hub_with_one_segment(tmp_path)
    orphan_chunk = _ingest_chunk(hub, pointer_token="default:2")
    _push(hub, orphan_chunk, segment_id="sg_orphan")
    deleted = hub.client.request("DELETE", f"/api/chunks/{orphan_chunk}", json={"by": "test"})
    assert deleted.status_code == 202, deleted.text

    service = hub.services.event_derivation_service
    # the orphan must be a candidate, or its exclusion from `derived` proves nothing
    assert {"sg_1", "sg_orphan"} <= set(service.candidate_segment_ids())
    outcome = service.re_derive(ReDeriveScope(), limit=10)

    assert outcome.derived == 1
