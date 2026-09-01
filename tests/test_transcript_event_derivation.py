"""Transcript-event derivation convergence: a finalized segment's events appear with no
manual step, a second pass writes nothing new, a version bump re-derives history while
leaving the prior version's rows intact, a superseded segment's rows are dropped, and a
content-hole segment re-derives once its record is accepted (blizzard#254, Phase 3 —
component tier)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import delete, select

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.analytics.derivation import EventDerivationReconciler, EventDerivationService
from blizzard.hub.domain.analytics.extraction import EXTRACTOR_VERSION, KIND_FILE_READ
from blizzard.hub.domain.transcripts import SegmentRecord
from blizzard.hub.domain.work import Chunk
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_store import ChunkStore
from blizzard.hub.store.internal.transcript_event_store import TranscriptEventStore
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore
from tests.support import hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 8, 12, tzinfo=UTC)


def _turns_json(*, path: str = "a.py") -> str:
    return json.dumps(
        [
            {
                "index": 0,
                "kind": "tool",
                "timestamp": "2026-08-12T09:00:00Z",
                "text": "",
                "tool": {
                    "name": "Read",
                    "input": {"file_path": path},
                    "input_unparsed": None,
                    "input_shape": "object",
                    "tool_use_id": "t1",
                    "output": None,
                    "output_truncated": False,
                },
                "thinking_redacted": False,
                "sidechain": None,
                "truncated": False,
            }
        ]
    )


def _segment_record(**overrides: object) -> SegmentRecord:
    values: dict[str, object] = {
        "segment_id": "sg_1",
        "chunk_id": "ch_1",
        "node_id": "nd_build",
        "epoch": 1,
        "spawn_generation": 1,
        "runner_id": "r1",
        "turn_range_start": 0,
        "turn_range_end": 0,
        "final": True,
        "normalizer_version": "claude-code-jsonl/2",
        "harness_version": "claude-code-1.0",
        "record_truncated": False,
        "turns_json": _turns_json(),
    }
    values.update(overrides)
    return SegmentRecord(**values)  # type: ignore[arg-type]


class _Fixture:
    def __init__(self, tmp_path: Path) -> None:
        db_url = f"sqlite:///{tmp_path / 'hub.db'}"
        migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
        self.engine = create_engine_from_url(db_url)
        self.chunks = ChunkStore(hub_store_connections(self.engine), FixedClock(_NOW))
        self.segments = TranscriptSegmentStore(hub_store_connections(self.engine))
        self.events = TranscriptEventStore(hub_store_connections(self.engine))
        self.clock = FixedClock(_NOW)
        self.chunks.mint(Chunk(chunk_id="ch_1", graph_id="gr_mint", work_refs=[], minted_at=_NOW))
        self.chunks.record_transition(
            transition_id="tr_1",
            chunk_id="ch_1",
            from_node_id=None,
            to_node_id="nd_build",
            choice_name=None,
            epoch=1,
            runner_id="r1",
            at=_NOW,
            artifacts=[],
            proposals=[],
        )
        self.service = EventDerivationService(
            events=self.events, facts=self.chunks, record=self.chunks, clock=self.clock
        )
        self.reconciler = EventDerivationReconciler(service=self.service, events=self.events)

    def mint_chunk(self, chunk_id: str, *, node_id: str = "nd_build") -> None:
        self.chunks.mint(Chunk(chunk_id=chunk_id, graph_id="gr_mint", work_refs=[], minted_at=_NOW))
        self.chunks.record_transition(
            transition_id=f"tr_{chunk_id}",
            chunk_id=chunk_id,
            from_node_id=None,
            to_node_id=node_id,
            choice_name=None,
            epoch=1,
            runner_id="r1",
            at=_NOW,
            artifacts=[],
            proposals=[],
        )

    def drop_chunk_row(self, chunk_id: str) -> None:
        """The breach ``hub:segment-chunk-resolves`` names, injected directly: sqlite
        enforces no foreign key, so a segment can outlive the chunk row it declares."""
        with self.engine.begin() as conn:
            conn.execute(delete(s.chunks).where(s.chunks.c.chunk_id == chunk_id))

    def stored_events(self) -> list[Any]:
        with self.engine.connect() as conn:
            return list(conn.execute(select(s.transcript_events)).all())


@pytest.fixture
def fixture(tmp_path: Path) -> _Fixture:
    return _Fixture(tmp_path)


def test_a_finalized_segments_events_appear_with_no_manual_step(fixture: _Fixture) -> None:
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)

    fixture.reconciler.sweep()

    rows = fixture.stored_events()
    assert len(rows) == 1
    assert rows[0].kind == KIND_FILE_READ
    assert rows[0].chunk_id == "ch_1"
    assert rows[0].node_id == "nd_build"
    assert rows[0].epoch == 1


def test_the_derived_events_graph_id_resolves_from_the_matching_transition(fixture: _Fixture) -> None:
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)

    fixture.reconciler.sweep()

    [row] = fixture.stored_events()
    assert row.graph_id == "gr_mint"  # the only transition recorded — no migration in this fixture


def test_candidate_segment_ids_narrows_to_the_given_chunk(fixture: _Fixture) -> None:
    """The re-derive route's chunk-scoped call (blizzard#254 D7)."""
    fixture.mint_chunk("ch_2")
    fixture.segments.insert_accepted(
        _segment_record(segment_id="sg_1", chunk_id="ch_1"), byte_count=10, codec="zlib", at=_NOW
    )
    fixture.segments.insert_accepted(
        _segment_record(segment_id="sg_2", chunk_id="ch_2"), byte_count=10, codec="zlib", at=_NOW
    )

    assert fixture.service.candidate_segment_ids(chunk_id="ch_1") == ["sg_1"]
    assert fixture.service.candidate_segment_ids(chunk_id="ch_2") == ["sg_2"]
    assert set(fixture.service.candidate_segment_ids()) == {"sg_1", "sg_2"}


def test_a_second_sweep_pass_writes_nothing_new(fixture: _Fixture) -> None:
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)
    fixture.reconciler.sweep()
    first_pass = fixture.stored_events()

    fixture.reconciler.sweep()

    assert fixture.stored_events() == first_pass


_NEXT_EXTRACTOR_VERSION = f"{EXTRACTOR_VERSION}-next"  # a version distinct from the current default


def test_a_version_bump_re_derives_history_leaving_the_prior_version_intact(fixture: _Fixture) -> None:
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)
    fixture.reconciler.sweep()
    marker_v1 = fixture.events.derivation_marker("sg_1", EXTRACTOR_VERSION)
    assert marker_v1 is not None

    bumped_service = EventDerivationService(
        events=fixture.events,
        facts=fixture.chunks,
        record=fixture.chunks,
        clock=fixture.clock,
        extractor_version=_NEXT_EXTRACTOR_VERSION,
    )
    bumped_reconciler = EventDerivationReconciler(service=bumped_service, events=fixture.events)
    bumped_reconciler.sweep()

    with fixture.engine.connect() as conn:
        rows = conn.execute(select(s.transcript_events)).all()
    versions = {row.extractor_version for row in rows}
    assert versions == {EXTRACTOR_VERSION, _NEXT_EXTRACTOR_VERSION}
    assert fixture.events.derivation_marker("sg_1", EXTRACTOR_VERSION) == marker_v1


def test_a_superseded_segments_rows_are_dropped(fixture: _Fixture) -> None:
    fixture.segments.insert_accepted(_segment_record(segment_id="sg_old"), byte_count=10, codec="zlib", at=_NOW)
    fixture.reconciler.sweep()
    assert fixture.events.derivation_marker("sg_old", EXTRACTOR_VERSION) is not None

    fixture.segments.insert_accepted(
        _segment_record(segment_id="sg_new", supersedes="sg_old"), byte_count=10, codec="zlib", at=_NOW
    )
    fixture.reconciler.sweep()

    assert fixture.events.derivation_marker("sg_old", EXTRACTOR_VERSION) is None
    with fixture.engine.connect() as conn:
        rows = conn.execute(select(s.transcript_events).where(s.transcript_events.c.segment_id == "sg_old")).all()
    assert rows == []


def test_derive_segment_reports_true_for_a_segment_with_derivation_input(fixture: _Fixture) -> None:
    """The re-derive route's segment-scoped branch consults this bit (blizzard#321) —
    a derivable segment reports it actually derived."""
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)

    assert fixture.service.derive_segment("sg_1") is True


def test_derive_segment_reports_false_for_a_segment_with_no_derivation_input(fixture: _Fixture) -> None:
    """An unknown segment id — a typo, a stale id, one never ingested — is the no-op the
    route must not report as ``derived: 1`` (blizzard#321)."""
    assert fixture.service.derive_segment("sg_does_not_exist") is False
    assert fixture.stored_events() == []


def test_a_content_hole_segment_derives_incomplete_then_re_derives_once_accepted(fixture: _Fixture) -> None:
    record = _segment_record()
    fixture.segments.insert_rejected(record, byte_count=999, reason="record_too_large", at=_NOW)

    fixture.reconciler.sweep()

    marker = fixture.events.derivation_marker("sg_1", EXTRACTOR_VERSION)
    assert marker is not None
    assert marker.complete is False
    assert marker.event_count == 0

    fixture.segments.update_to_accepted(record, byte_count=10, codec="zlib", at=_NOW)
    fixture.reconciler.sweep()

    marker_after = fixture.events.derivation_marker("sg_1", EXTRACTOR_VERSION)
    assert marker_after is not None
    assert marker_after.complete is True
    assert marker_after.event_count == 1


# --- a segment whose chunk no chunk read shows leaves the derivation set entirely ---


def test_a_segment_whose_chunk_was_never_minted_is_not_a_candidate(fixture: _Fixture) -> None:
    """A segment can outlive — or never have had — its chunk row, and the sweep must
    converge over it rather than re-reaching an underivable segment every tick."""
    fixture.segments.insert_accepted(
        _segment_record(segment_id="sg_orphan", chunk_id="ch_never_minted"), byte_count=10, codec="zlib", at=_NOW
    )

    fixture.reconciler.sweep()

    assert fixture.events.visible_segment_ids() == frozenset()
    assert fixture.service.candidate_segment_ids() == []
    assert fixture.stored_events() == []


def test_a_segment_that_outlives_its_chunk_row_has_its_derived_rows_dropped(fixture: _Fixture) -> None:
    """Rows derived before the breach do not survive it: the sweep's own drop path
    reclaims a segment the visible set no longer holds."""
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)
    fixture.reconciler.sweep()
    assert len(fixture.stored_events()) == 1

    fixture.drop_chunk_row("ch_1")
    fixture.reconciler.sweep()

    assert fixture.events.visible_segment_ids() == frozenset()
    assert fixture.stored_events() == []
    assert fixture.events.derivation_marker("sg_1", EXTRACTOR_VERSION) is None


def test_forcing_a_segment_whose_chunk_does_not_resolve_is_a_no_op_rather_than_a_crash(fixture: _Fixture) -> None:
    """The segment-scoped re-derive route forces a segment regardless of candidacy, so it
    reaches one the visible set excludes and must decline it rather than raise."""
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)
    fixture.drop_chunk_row("ch_1")

    assert fixture.service.derive_segment("sg_1") is False
    assert fixture.stored_events() == []


class _PoisonedService(EventDerivationService):
    """Raises on one segment id, standing in for any segment the sweep cannot derive."""

    def __init__(self, *, poison: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._poison = poison

    def derive_segment(self, segment_id: str) -> bool:
        if segment_id == self._poison:
            raise RuntimeError("underivable")
        return super().derive_segment(segment_id)


def test_one_underivable_segment_does_not_cost_the_rest_of_the_tick(fixture: _Fixture) -> None:
    """A segment that raises is stepped over, not fatal — every later candidate still
    derives."""
    fixture.mint_chunk("ch_2")
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)
    fixture.segments.insert_accepted(
        _segment_record(segment_id="sg_2", chunk_id="ch_2"), byte_count=10, codec="zlib", at=_NOW
    )
    poisoned = _PoisonedService(
        poison="sg_1", events=fixture.events, facts=fixture.chunks, record=fixture.chunks, clock=fixture.clock
    )

    EventDerivationReconciler(service=poisoned, events=fixture.events).sweep()

    assert [row.segment_id for row in fixture.stored_events()] == ["sg_2"]
