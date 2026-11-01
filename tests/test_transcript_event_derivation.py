"""Transcript-event derivation convergence: a finalized segment's events appear with no
manual step, a second pass writes nothing new, a version bump re-derives history while
leaving the prior version's rows intact, a superseded segment's rows are dropped, and a
content-hole segment re-derives once its record is accepted (component tier)."""

from __future__ import annotations

import json
import zlib
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store import batching as batching_module
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.chunk.model import Chunk, decide_close_enqueue
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.observability.analytics.derivation import (
    EventDerivationReconciler,
    EventDerivationService,
    GraphPins,
    TenantEventDerivation,
)
from blizzard.hub.domain.observability.analytics.events import KIND_FILE_READ
from blizzard.hub.domain.observability.analytics.extraction import EXTRACTOR_VERSION
from blizzard.hub.domain.observability.transcripts import TranscriptSlice
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from blizzard.hub.store.internal.chunk_record_store import ChunkRecordStore
from blizzard.hub.store.internal.hub_scoped_corpus import SweepCorpusReads
from blizzard.hub.store.internal.hub_scoped_reads import hub_scoped_read
from blizzard.hub.store.internal.transcript_event_store import TranscriptEventStore
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore
from tests.support import (
    chunk_stores,
    count_queries,
    count_rows_read,
    hub_level_connections,
    hub_store_connections,
    migrated_hub_at,
    seed_chunk_record,
    seed_lease,
)

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


def _segment_record(**overrides: object) -> TranscriptSlice:
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
    return TranscriptSlice(**values)  # type: ignore[arg-type]


class _Fixture:
    def __init__(self, tmp_path: Path) -> None:
        db_url = f"sqlite:///{tmp_path / 'hub.db'}"
        migrated_hub_at(db_url)
        self.engine = create_engine_from_url(db_url)
        self.chunks = chunk_stores(self.engine, FixedClock(_NOW))
        self.segments = TranscriptSegmentStore(hub_store_connections(self.engine))
        self.events = TranscriptEventStore(hub_store_connections(self.engine))
        self.clock = FixedClock(_NOW)
        self.corpus = SweepCorpusReads(partial(hub_scoped_read, hub_level_connections(self.engine)))
        seed_chunk_record(self.chunks, Chunk(chunk_id="ch_1", graph_id="gr_mint", work_refs=[], minted_at=_NOW))
        self.chunks.movement.record_transition(
            transition_id="tr_1",
            close_decision=decide_close_enqueue([], lambda _: True),
            chunk_id="ch_1",
            from_node_id=None,
            to_node_id="nd_build",
            choice_name=None,
            epoch=1,
            runner_id="r1",
            at=_NOW,
            artifacts=[],
            admission=EpochAdmission.AT_OR_ABOVE,
        )
        self.service = EventDerivationService(
            events=self.events, facts=self.chunks.facts, record=self.chunks.record, clock=self.clock
        )
        self.reconciler = self.reconciler_over(self.service)

    def reconciler_over(self, service: EventDerivationService) -> EventDerivationReconciler:
        """A fresh hub pass, as a restart builds, deriving the fixture's one tenant through ``service``."""
        tenant = TenantEventDerivation(service=service, events=self.events, clock=self.clock)
        return EventDerivationReconciler(
            corpus=self.corpus,
            tenant=lambda _tenant: tenant,
            clock=self.clock,
            extractor_version=service.extractor_version,
        )

    def mint_chunk(self, chunk_id: str, *, node_id: str = "nd_build") -> None:
        seed_chunk_record(self.chunks, Chunk(chunk_id=chunk_id, graph_id="gr_mint", work_refs=[], minted_at=_NOW))
        self.chunks.movement.record_transition(
            transition_id=f"tr_{chunk_id}",
            close_decision=decide_close_enqueue([], lambda _: True),
            chunk_id=chunk_id,
            from_node_id=None,
            to_node_id=node_id,
            choice_name=None,
            epoch=1,
            runner_id="r1",
            at=_NOW,
            artifacts=[],
            admission=EpochAdmission.AT_OR_ABOVE,
        )

    def drop_chunk_row(self, chunk_id: str) -> None:
        """The breach ``hub:segment-chunk-resolves`` names, injected directly: sqlite
        enforces no foreign key, so a segment can outlive the chunk row it declares."""
        with self.engine.begin() as conn:
            conn.execute(hub_store_connections(self.engine).delete(s.chunks).where(s.chunks.c.chunk_id == chunk_id))

    def stored_events(self) -> list[Any]:
        with self.engine.connect() as conn:
            return list(conn.execute(hub_store_connections(self.engine).select(s.transcript_events)).all())


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


def test_a_derived_events_provenance_matches_its_segments_own(fixture: _Fixture) -> None:
    """The segment's own frozen harness identity rides every event
    that segment derives, read straight off `transcript_segments`."""
    fixture.segments.insert_accepted(
        _segment_record(harness_id="claude_code", model="claude-sonnet-5", effort="high"),
        byte_count=10,
        codec="zlib",
        at=_NOW,
    )

    fixture.reconciler.sweep()

    [row] = fixture.stored_events()
    assert row.harness_id == "claude_code"
    assert row.harness_version == "claude-code-1.0"
    assert row.model == "claude-sonnet-5"
    assert row.effort == "high"


def test_the_derived_events_graph_id_resolves_from_the_matching_transition(fixture: _Fixture) -> None:
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)

    fixture.reconciler.sweep()

    [row] = fixture.stored_events()
    assert row.graph_id == "gr_mint"  # the only transition recorded — no migration in this fixture


def test_the_derived_events_graph_id_falls_back_to_the_mint_pin_with_no_matching_transition(
    fixture: _Fixture,
) -> None:
    """A chunk with no transition matching the segment's ``(node_id, epoch)`` resolves
    its graph via the mint-pin fallback — proven here through a chunk minted with
    no transition recorded at all."""
    seed_chunk_record(fixture.chunks, Chunk(chunk_id="ch_unmoved", graph_id="gr_unmoved", work_refs=[], minted_at=_NOW))
    fixture.segments.insert_accepted(
        _segment_record(segment_id="sg_unmoved", chunk_id="ch_unmoved"), byte_count=10, codec="zlib", at=_NOW
    )

    fixture.reconciler.sweep()

    [row] = [r for r in fixture.stored_events() if r.chunk_id == "ch_unmoved"]
    assert row.graph_id == "gr_unmoved"


class _CountingFactsStore(ChunkFactsStore):
    def __init__(self, store, clock) -> None:
        super().__init__(store, clock)
        self.load_facts_calls = 0
        self.load_facts_for_calls = 0
        self.movement_facts_for_calls = 0

    def load_facts(self, chunk_id: str):
        self.load_facts_calls += 1
        return super().load_facts(chunk_id)

    def load_facts_for(self, chunk_ids):
        self.load_facts_for_calls += 1
        return super().load_facts_for(chunk_ids)

    def movement_facts_for(self, chunk_ids):
        self.movement_facts_for_calls += 1
        return super().movement_facts_for(chunk_ids)


class _CountingRecordStore(ChunkRecordStore):
    def __init__(self, store, clock) -> None:
        super().__init__(store, clock)
        self.get_calls = 0
        self.graph_id_of_many_calls = 0

    def get(self, chunk_id: str):
        self.get_calls += 1
        return super().get(chunk_id)

    def graph_id_of_many(self, chunk_ids):
        self.graph_id_of_many_calls += 1
        return super().graph_id_of_many(chunk_ids)


def test_sweep_resolves_graph_pins_with_one_movement_facts_for_and_one_graph_id_of_many_call(
    fixture: _Fixture,
) -> None:
    """A pass over several segments spanning several chunks — including two on the same
    chunk — resolves every graph pin through one bulk `movement_facts_for` call and one bulk
    `graph_id_of_many` call, never the per-chunk `load_facts`/`record.get`."""
    for i in range(3):
        fixture.mint_chunk(f"ch_multi_{i}")
        fixture.segments.insert_accepted(
            _segment_record(segment_id=f"sg_multi_{i}", chunk_id=f"ch_multi_{i}"), byte_count=10, codec="zlib", at=_NOW
        )
    fixture.segments.insert_accepted(
        _segment_record(segment_id="sg_multi_0b", chunk_id="ch_multi_0"), byte_count=10, codec="zlib", at=_NOW
    )

    counting_facts = _CountingFactsStore(hub_store_connections(fixture.engine), fixture.clock)
    counting_record = _CountingRecordStore(hub_store_connections(fixture.engine), fixture.clock)
    service = EventDerivationService(
        events=fixture.events, facts=counting_facts, record=counting_record, clock=fixture.clock
    )
    reconciler = fixture.reconciler_over(service)

    reconciler.sweep()

    assert len(fixture.stored_events()) == 4
    assert counting_facts.movement_facts_for_calls == 1
    assert counting_facts.load_facts_for_calls == 0
    assert counting_record.graph_id_of_many_calls == 1
    assert counting_facts.load_facts_calls == 0
    assert counting_record.get_calls == 0


def test_candidate_segment_ids_narrows_to_the_given_chunk(fixture: _Fixture) -> None:
    """The re-derive route's chunk-scoped call."""
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
        facts=fixture.chunks.facts,
        record=fixture.chunks.record,
        clock=fixture.clock,
        extractor_version=_NEXT_EXTRACTOR_VERSION,
    )
    bumped_reconciler = fixture.reconciler_over(bumped_service)
    bumped_reconciler.sweep()

    with fixture.engine.connect() as conn:
        rows = conn.execute(hub_store_connections(fixture.engine).select(s.transcript_events)).all()
    versions = {row.extractor_version for row in rows}
    assert versions == {EXTRACTOR_VERSION, _NEXT_EXTRACTOR_VERSION}
    assert fixture.events.derivation_marker("sg_1", EXTRACTOR_VERSION) == marker_v1


#: The version the current one superseded — its rows predate the provenance columns.
_PRIOR_EXTRACTOR_VERSION = "blizzard-analytics/3"


def test_the_bump_leaves_a_prior_versions_un_backfilled_rows_untouched(fixture: _Fixture) -> None:
    """The provenance columns land un-backfilled, so a row derived before the bump holds
    NULL for all four. Sweeping at the current version must neither restamp nor drop it:
    ``replace_segment_events`` scopes its delete and insert to one version's rows alone."""
    fixture.segments.insert_accepted(
        _segment_record(harness_id="claude_code", model="claude-sonnet-5", effort="high"),
        byte_count=10,
        codec="zlib",
        at=_NOW,
    )
    prior_service = EventDerivationService(
        events=fixture.events,
        facts=fixture.chunks.facts,
        record=fixture.chunks.record,
        clock=fixture.clock,
        extractor_version=_PRIOR_EXTRACTOR_VERSION,
    )
    fixture.reconciler_over(prior_service).sweep()
    with fixture.engine.begin() as conn:
        conn.execute(
            hub_store_connections(fixture.engine)
            .update(s.transcript_events)
            .where(s.transcript_events.c.extractor_version == _PRIOR_EXTRACTOR_VERSION)
            .values(harness_id=None, harness_version=None, model=None, effort=None)
        )
    prior_rows = [row for row in fixture.stored_events() if row.extractor_version == _PRIOR_EXTRACTOR_VERSION]
    prior_marker = fixture.events.derivation_marker("sg_1", _PRIOR_EXTRACTOR_VERSION)
    assert prior_rows and prior_marker is not None

    fixture.reconciler.sweep()

    rows = fixture.stored_events()
    assert [row for row in rows if row.extractor_version == _PRIOR_EXTRACTOR_VERSION] == prior_rows
    assert fixture.events.derivation_marker("sg_1", _PRIOR_EXTRACTOR_VERSION) == prior_marker
    bumped = [row for row in rows if row.extractor_version == EXTRACTOR_VERSION]
    assert bumped
    for row in bumped:
        assert (row.harness_id, row.harness_version, row.model, row.effort) == (
            "claude_code",
            "claude-code-1.0",
            "claude-sonnet-5",
            "high",
        )


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
        rows = conn.execute(
            hub_store_connections(fixture.engine)
            .select(s.transcript_events)
            .where(s.transcript_events.c.segment_id == "sg_old")
        ).all()
    assert rows == []


def test_derive_segment_reports_true_for_a_segment_with_derivation_input(fixture: _Fixture) -> None:
    """The re-derive route's segment-scoped branch consults this bit —
    a derivable segment reports it actually derived."""
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)

    pins = fixture.service.graph_pins_for(["sg_1"])
    assert fixture.service.derive_segment("sg_1", pins) is True


def test_derive_segment_reports_false_for_a_segment_with_no_derivation_input(fixture: _Fixture) -> None:
    """An unknown segment id — a typo, a stale id, one never ingested — is the no-op the
    route must not report as ``derived: 1``."""
    pins = fixture.service.graph_pins_for(["sg_does_not_exist"])
    assert fixture.service.derive_segment("sg_does_not_exist", pins) is False
    assert fixture.stored_events() == []


def test_a_content_hole_segment_derives_incomplete_then_re_derives_once_accepted(fixture: _Fixture) -> None:
    record = _segment_record()
    fixture.segments.insert_rejected(record, byte_count=999, reason="record_too_large", at=_NOW)

    fixture.reconciler.sweep()

    marker = fixture.events.derivation_marker("sg_1", EXTRACTOR_VERSION)
    assert marker is not None
    assert marker.complete is False
    assert marker.event_count == 0

    # A later instant than the rejected insert's, not `_NOW` again: the change probe reads
    # `received_at` moving forward as its signal a record was rewritten.
    fixture.segments.update_to_accepted(record, byte_count=10, codec="zlib", at=_NOW + timedelta(seconds=1))
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


def test_a_dropped_segment_leaves_one_drop_fact_stamped_by_the_clock_with_no_egress_configured(
    fixture: _Fixture,
) -> None:
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)
    fixture.reconciler.sweep()
    fixture.drop_chunk_row("ch_1")
    fixture.clock.advance(timedelta(minutes=5))
    later = fixture.clock.now()

    fixture.reconciler.sweep()

    with fixture.engine.connect() as conn:
        rows = conn.execute(hub_store_connections(fixture.engine).select(s.transcript_event_drops)).all()
    assert [(r.segment_id, r.chunk_id, r.epoch, r.spawn_generation, r.dropped_at) for r in rows] == [
        ("sg_1", "ch_1", 1, 1, later)
    ]


def test_forcing_a_segment_whose_chunk_does_not_resolve_is_a_no_op_rather_than_a_crash(fixture: _Fixture) -> None:
    """The segment-scoped re-derive route forces a segment regardless of candidacy, so it
    reaches one the visible set excludes and must decline it rather than raise."""
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)
    fixture.drop_chunk_row("ch_1")

    pins = fixture.service.graph_pins_for(["sg_1"])
    assert fixture.service.derive_segment("sg_1", pins) is False
    assert fixture.stored_events() == []


class _PoisonedService(EventDerivationService):
    """Raises on one segment id, standing in for any segment the sweep cannot derive."""

    def __init__(self, *, poison: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._poison = poison

    def derive_segment(self, segment_id: str, pins: GraphPins) -> bool:
        if segment_id == self._poison:
            raise RuntimeError("underivable")
        return super().derive_segment(segment_id, pins)

    def derive_input(self, segment_id: str, current: Any, pins: GraphPins) -> bool:
        if segment_id == self._poison:
            raise RuntimeError("underivable")
        return super().derive_input(segment_id, current, pins)


def test_one_underivable_segment_does_not_cost_the_rest_of_the_tick(fixture: _Fixture) -> None:
    """A segment that raises is stepped over, not fatal — every later candidate still
    derives."""
    fixture.mint_chunk("ch_2")
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)
    fixture.segments.insert_accepted(
        _segment_record(segment_id="sg_2", chunk_id="ch_2"), byte_count=10, codec="zlib", at=_NOW
    )
    poisoned = _PoisonedService(
        poison="sg_1",
        events=fixture.events,
        facts=fixture.chunks.facts,
        record=fixture.chunks.record,
        clock=fixture.clock,
    )

    fixture.reconciler_over(poisoned).sweep()

    assert [row.segment_id for row in fixture.stored_events()] == ["sg_2"]


class _PoisonedGraphPinsService(EventDerivationService):
    """Raises out of ``graph_pins_for``, standing in for any pin-resolution fault."""

    def graph_pins_for(self, segment_ids: Sequence[str]) -> GraphPins:
        raise RuntimeError("pin resolution unavailable")


def test_a_pin_resolution_fault_skips_the_whole_pass_without_raising(fixture: _Fixture) -> None:
    """``graph_pins_for`` raising costs the entire tick, not one segment: no candidate
    derives, and the sweep itself does not raise."""
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)
    poisoned = _PoisonedGraphPinsService(
        events=fixture.events,
        facts=fixture.chunks.facts,
        record=fixture.chunks.record,
        clock=fixture.clock,
    )

    fixture.reconciler_over(poisoned).sweep()

    assert fixture.stored_events() == []


# --- bulk candidacy, zero decode, batched drop -----------------


def test_a_steady_state_pass_decompresses_no_content_and_holds_a_flat_statement_count(
    fixture: _Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance criterion 1 — a steady-state pass decompresses zero bytes and issues a
    statement count independent of segment count."""
    for i in range(5):
        fixture.mint_chunk(f"ch_small_{i}")
        fixture.segments.insert_accepted(
            _segment_record(segment_id=f"sg_small_{i}", chunk_id=f"ch_small_{i}"), byte_count=10, codec="zlib", at=_NOW
        )
    fixture.reconciler.sweep()  # builds every marker — not the steady-state pass under test

    def _boom(*_: object, **__: object) -> bytes:
        raise AssertionError("a steady-state pass must not decompress any content")

    # Past the derivation change probe's forced floor, so each timed
    # sweep below runs the real candidacy read rather than the probe's cheap
    # unchanged-signature skip — the skip alone would make small_count == large_count
    # trivially true without ever exercising the bulk read this test targets.
    fixture.clock.advance(timedelta(minutes=11))
    monkeypatch.setattr(zlib, "decompress", _boom)
    small_count = count_queries(fixture.engine, fixture.reconciler.sweep)
    monkeypatch.undo()

    for i in range(5, 55):
        fixture.mint_chunk(f"ch_large_{i}")
        fixture.segments.insert_accepted(
            _segment_record(segment_id=f"sg_large_{i}", chunk_id=f"ch_large_{i}"), byte_count=10, codec="zlib", at=_NOW
        )
    fixture.reconciler.sweep()  # builds the 50 new markers too — still not the timed pass

    fixture.clock.advance(timedelta(minutes=11))
    monkeypatch.setattr(zlib, "decompress", _boom)
    large_count = count_queries(fixture.engine, fixture.reconciler.sweep)

    assert small_count == large_count


def test_the_drop_pass_never_calls_visible_segment_ids_a_second_time(
    fixture: _Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance criterion 4 — ``visible_segment_ids`` is evaluated once per pass: the
    candidacy read's own bulk statement is the pass's only visibility evaluation, and the
    drop pass reuses its result rather than calling ``visible_segment_ids`` again."""
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)
    fixture.reconciler.sweep()

    def _boom(*_: object, **__: object) -> frozenset[str]:
        raise AssertionError("the drop pass must reuse candidacy's visible set, not recompute it")

    monkeypatch.setattr(TranscriptEventStore, "visible_segment_ids", _boom)

    fixture.reconciler.sweep()  # raises if the drop pass re-evaluates visibility


def test_stale_segments_drop_in_one_batched_pass_not_one_per_segment(fixture: _Fixture) -> None:
    """Acceptance criterion 4 — stale segments drop in one transaction regardless of how
    many there are."""
    for i in range(3):
        fixture.mint_chunk(f"ch_drop_{i}")
        fixture.segments.insert_accepted(
            _segment_record(segment_id=f"sg_drop_{i}", chunk_id=f"ch_drop_{i}"), byte_count=10, codec="zlib", at=_NOW
        )
    fixture.reconciler.sweep()
    for i in range(3):
        fixture.drop_chunk_row(f"ch_drop_{i}")
    small_count = count_queries(fixture.engine, fixture.reconciler.sweep)
    assert fixture.events.derived_segment_ids() == frozenset()

    for i in range(3, 33):
        fixture.mint_chunk(f"ch_drop_{i}")
        fixture.segments.insert_accepted(
            _segment_record(segment_id=f"sg_drop_{i}", chunk_id=f"ch_drop_{i}"), byte_count=10, codec="zlib", at=_NOW
        )
    fixture.reconciler.sweep()
    for i in range(3, 33):
        fixture.drop_chunk_row(f"ch_drop_{i}")
    large_count = count_queries(fixture.engine, fixture.reconciler.sweep)

    assert small_count == large_count
    assert fixture.events.derived_segment_ids() == frozenset()


def test_an_extractor_version_bump_decodes_each_segment_at_most_once(
    fixture: _Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Acceptance criterion 3 — the candidate pass never decodes, so the only decode a
    version bump's full-corpus re-derive pays is `derive_segment`'s own, once per segment."""
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)
    fixture.reconciler.sweep()

    decompress_calls = 0
    real_decompress = zlib.decompress

    def _counting(*args: object, **kwargs: object) -> bytes:
        nonlocal decompress_calls
        decompress_calls += 1
        return real_decompress(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(zlib, "decompress", _counting)

    bumped_service = EventDerivationService(
        events=fixture.events,
        facts=fixture.chunks.facts,
        record=fixture.chunks.record,
        clock=fixture.clock,
        extractor_version=f"{EXTRACTOR_VERSION}-next",
    )
    fixture.reconciler_over(bumped_service).sweep()

    assert decompress_calls == 1


# --- the change probe and its forced floor ------------------


def test_an_unchanged_signature_skips_the_pass_entirely(fixture: _Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    """Acceptance criterion 6 — a second pass over an unchanged store does not evaluate
    candidacy at all, not merely find nothing to derive."""
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)
    fixture.reconciler.sweep()  # the first-ever pass always runs full and records the signature

    def _boom(*_: object, **__: object) -> Any:
        raise AssertionError("an unchanged signature must skip the full pass, not merely find no candidates")

    monkeypatch.setattr(fixture.service, "candidacy", _boom)

    fixture.reconciler.sweep()  # raises unless the skip is honored


def test_a_changed_signature_triggers_a_full_pass(fixture: _Fixture) -> None:
    """A new segment landing moves the signature's row count and max id — the probe must
    catch it rather than treat it as steady state."""
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)
    fixture.reconciler.sweep()

    fixture.mint_chunk("ch_2")
    fixture.segments.insert_accepted(
        _segment_record(segment_id="sg_2", chunk_id="ch_2"), byte_count=10, codec="zlib", at=_NOW
    )
    fixture.reconciler.sweep()

    assert fixture.events.derivation_marker("sg_2", EXTRACTOR_VERSION) is not None


def test_a_fresh_reconcilers_first_sweep_always_runs_full(fixture: _Fixture) -> None:
    """A fresh process start holds no prior signature — it must never mistake that absence
    for 'nothing changed' and skip its very first pass (this is also what covers an
    ``EXTRACTOR_VERSION`` bump, which changes derivation markers, not this signature)."""
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)

    fresh_reconciler = fixture.reconciler_over(fixture.service)
    fresh_reconciler.sweep()

    assert fixture.events.derivation_marker("sg_1", EXTRACTOR_VERSION) is not None


def test_the_forced_floor_runs_a_full_pass_after_ten_minutes_despite_an_unchanged_signature(
    fixture: _Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The probe is an optimization only — correctness rests on this floor, so a
    same-instant rewrite the signature misses is still picked up within one floor period."""
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)
    fixture.reconciler.sweep()  # the first-ever pass — records the signature and the floor instant

    calls = 0
    real_candidacy = fixture.corpus.candidacy

    def _counting(extractor_version: str) -> Any:
        nonlocal calls
        calls += 1
        return real_candidacy(extractor_version)

    monkeypatch.setattr(fixture.corpus, "candidacy", _counting)

    fixture.reconciler.sweep()  # unchanged signature, floor not yet due — skipped
    assert calls == 0

    fixture.clock.advance(timedelta(minutes=10))
    fixture.reconciler.sweep()  # unchanged signature, but the floor is now due
    assert calls == 1


# --- batched input decoding -----------------


class _SpyEvents(TranscriptEventStore):
    """Records, in one ordered log, every input read and every replacement write."""

    def __init__(self, store: Any, log: list[tuple[str, tuple[str, ...]]], *, fail_plural: bool = False) -> None:
        super().__init__(store)
        self.log = log
        self._fail_plural = fail_plural

    def segment_derivation_inputs(self, segment_ids: Sequence[str]):
        self.log.append(("plural", tuple(segment_ids)))
        if self._fail_plural:
            raise RuntimeError("plural read down")
        return super().segment_derivation_inputs(segment_ids)

    def segment_derivation_input(self, segment_id: str):
        self.log.append(("single", (segment_id,)))
        return super().segment_derivation_input(segment_id)

    def replace_segment_events(self, segment_id: str, *args: Any, **kwargs: Any):
        self.log.append(("write", (segment_id,)))
        return super().replace_segment_events(segment_id, *args, **kwargs)


def _spied_reconciler(
    fixture: _Fixture, *, fail_plural: bool = False
) -> tuple[EventDerivationReconciler, list[tuple[str, tuple[str, ...]]]]:
    log: list[tuple[str, tuple[str, ...]]] = []
    spy = _SpyEvents(hub_store_connections(fixture.engine), log, fail_plural=fail_plural)
    service = EventDerivationService(
        events=spy, facts=fixture.chunks.facts, record=fixture.chunks.record, clock=fixture.clock
    )
    tenant = TenantEventDerivation(service=service, events=spy, clock=fixture.clock)
    reconciler = EventDerivationReconciler(
        corpus=fixture.corpus,
        tenant=lambda _t: tenant,
        clock=fixture.clock,
        extractor_version=service.extractor_version,
    )
    return reconciler, log


def _seed_ids(fixture: _Fixture, ids: Sequence[str]) -> None:
    for segment_id in ids:
        fixture.segments.insert_accepted(_segment_record(segment_id=segment_id), byte_count=10, codec="zlib", at=_NOW)


def test_convergence_reads_inputs_one_bounded_sub_batch_at_a_time(
    fixture: _Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Candidates spanning more than two sub-batches take one plural read per sub-batch, no read
    exceeds the batch size, each sub-batch's writes precede the next read, and no good segment is re-read."""
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 2)
    ids = [f"sg_{i}" for i in range(1, 6)]
    _seed_ids(fixture, ids[1:])
    fixture.segments.insert_accepted(_segment_record(), byte_count=10, codec="zlib", at=_NOW)
    reconciler, log = _spied_reconciler(fixture)

    reconciler.sweep()

    reads = [entry for entry in log if entry[0] == "plural"]
    assert len(reads) == 3
    assert all(len(ids_) <= 2 for _, ids_ in reads)
    assert not [entry for entry in log if entry[0] == "single"]
    kinds = [kind for kind, _ in log]
    assert kinds == ["plural", "write", "write", "plural", "write", "write", "plural", "write"]
    assert sorted(row.segment_id for row in fixture.stored_events()) == sorted(ids)


def test_a_missing_input_falls_back_to_the_singular_read_without_costing_neighbours(
    fixture: _Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A corrupt segment is counted failed through the singular path; a vanished one is not; good
    ids after the bad one and across a boundary still derive."""
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 2)
    _seed_ids(fixture, ["sg_a", "sg_b", "sg_c", "sg_d"])
    with fixture.engine.begin() as conn:
        conn.execute(
            hub_store_connections(fixture.engine)
            .update(s.transcript_segments)
            .where(s.transcript_segments.c.segment_id == "sg_a")
            .values(content=b"not zlib")
        )
    reconciler, log = _spied_reconciler(fixture)

    with capture_logs() as logs:
        reconciler.sweep()

    assert sorted(row.segment_id for row in fixture.stored_events()) == ["sg_b", "sg_c", "sg_d"]
    assert [entry["segment_id"] for entry in logs if entry["event"] == "segment derivation skipped"] == ["sg_a"]
    assert [entry for entry in log if entry[0] == "single"] == [("single", ("sg_a",))]


def test_a_failing_plural_read_reads_its_sub_batch_singly(fixture: _Fixture, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 2)
    ids = ["sg_a", "sg_b", "sg_c"]
    _seed_ids(fixture, ids)
    reconciler, log = _spied_reconciler(fixture, fail_plural=True)

    reconciler.sweep()

    assert sorted(row.segment_id for row in fixture.stored_events()) == ids
    assert [entry[1][0] for entry in log if entry[0] == "single"] == ids


def _deep_fixture(tmp_path: Path, depth: int) -> _Fixture:
    """A fixture whose chunks carry identical transitions but ``depth`` lease facts each."""
    tmp_path.mkdir()
    fixture = _Fixture(tmp_path)
    fixture.mint_chunk("ch_2", node_id="nd_other")
    for chunk_id in ("ch_1", "ch_2"):
        fixture.segments.insert_accepted(
            _segment_record(segment_id=f"sg_{chunk_id}", chunk_id=chunk_id), byte_count=10, codec="zlib", at=_NOW
        )
        for epoch in range(2, 2 + depth):
            seed_lease(fixture.engine, chunk_id, epoch=epoch, runner_id="r1", at=_NOW)
    return fixture


def test_graph_pins_read_only_the_movement_families_and_answer_as_the_wide_read_does(tmp_path: Path) -> None:
    """Deeper histories in a family the pins never consume change neither the answer nor the rows read."""
    shallow = _deep_fixture(tmp_path / "shallow", 1)
    deep = _deep_fixture(tmp_path / "deep", 20)
    segment_ids = ["sg_ch_1", "sg_ch_2", "sg_missing"]

    def pins_of(fixture: _Fixture) -> dict[str, str | None]:
        pins = fixture.service.graph_pins_for(segment_ids)
        return {
            "ch_1": pins.graph_id_for("ch_1", "nd_build", 1),
            "ch_2": pins.graph_id_for("ch_2", "nd_other", 1),
            "ch_2_unmatched": pins.graph_id_for("ch_2", "nd_build", 9),
            "ch_unknown": pins.graph_id_for("ch_unknown", "nd_build", 1),
        }

    assert pins_of(shallow) == pins_of(deep)
    shallow_rows = count_rows_read(shallow.engine, lambda: shallow.service.graph_pins_for(segment_ids))
    deep_rows = count_rows_read(deep.engine, lambda: deep.service.graph_pins_for(segment_ids))
    assert shallow_rows == deep_rows

    chunk_ids = ["ch_1", "ch_2"]
    narrow = count_queries(deep.engine, lambda: deep.chunks.facts.movement_facts_for(chunk_ids))
    wide = count_queries(deep.engine, lambda: deep.chunks.facts.load_facts_for(chunk_ids))
    assert narrow < wide
