"""The analytics derivation's decisions — the re-derive scope, when a sweep pass is due, event
stamping, candidacy, completeness, and the version a read covers — pinned by value."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.domain.observability.analytics.derivation import (
    FORCED_FULL_PASS_FLOOR,
    EventDerivationService,
    GraphPins,
    ReDeriveOutcome,
    ReDeriveScope,
    ReDeriveScopeRefused,
    derivation_due,
    stamp_events,
)
from blizzard.hub.domain.observability.analytics.events import (
    DerivationSignature,
    SegmentDerivationInput,
    SegmentProvenance,
    is_candidate,
    segment_complete,
)
from blizzard.hub.domain.observability.analytics.extraction import EXTRACTOR_VERSION, ExtractedEvent, read_version

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)


# --- the re-derive scope -----------------------------------------------------------------------


def test_a_re_derive_naming_both_a_segment_and_a_chunk_is_refused() -> None:
    with pytest.raises(ReDeriveScopeRefused, match="segment_id and chunk_id are mutually exclusive"):
        ReDeriveScope.of(segment_id="sg_1", chunk_id="ch_1")


@pytest.mark.parametrize(("segment_id", "chunk_id"), [("sg_1", None), (None, "ch_1"), (None, None)])
def test_a_re_derive_names_one_segment_one_chunk_or_everything(segment_id: str | None, chunk_id: str | None) -> None:
    assert ReDeriveScope.of(segment_id=segment_id, chunk_id=chunk_id) == ReDeriveScope(segment_id, chunk_id)


@pytest.mark.parametrize(
    ("candidates", "limit", "batch", "remaining"),
    [([], 5, [], 0), (["a", "b", "c"], 2, ["a", "b"], 1), (["a", "b"], 5, ["a", "b"], 0)],
)
def test_a_re_derive_takes_the_first_limit_candidates(
    candidates: list[str], limit: int, batch: list[str], remaining: int
) -> None:
    assert ReDeriveScope().batch(candidates, limit) == (batch, remaining)


class _Service(EventDerivationService):
    """The service's own re-derive over canned reads: ``derivable`` names the segments that derive."""

    def __init__(self, candidates: list[str], derivable: set[str]) -> None:
        self._candidates = candidates
        self._derivable = derivable

    def candidate_segment_ids(self, *, chunk_id: str | None = None) -> list[str]:
        return list(self._candidates)

    def graph_pins_for(self, segment_ids: Sequence[str]) -> GraphPins:
        return GraphPins()

    def derive_segment(self, segment_id: str, pins: GraphPins) -> bool:
        return segment_id in self._derivable


def test_a_re_derive_counts_only_the_candidates_it_actually_derived() -> None:
    service = _Service(["a", "b", "c"], derivable={"a"})
    assert service.re_derive(ReDeriveScope(), limit=2) == ReDeriveOutcome(derived=1, remaining=1)


def test_a_segment_re_derive_reports_whether_it_derived() -> None:
    service = _Service([], derivable={"sg_1"})
    assert service.re_derive(ReDeriveScope(segment_id="sg_1"), limit=1) == ReDeriveOutcome(1, 0)
    assert service.re_derive(ReDeriveScope(segment_id="sg_gone"), limit=1) == ReDeriveOutcome(0, 0)


# --- the sweep's change probe ------------------------------------------------------------------


_SIGNATURE = DerivationSignature(segment_count=3, max_segment_id=9, max_received_at=_NOW, chunk_count=2)


def test_a_fresh_process_always_runs_a_pass() -> None:
    assert derivation_due(None, _SIGNATURE, None, _NOW) is True


def test_an_unchanged_signature_skips_until_the_floor_is_due() -> None:
    last = _NOW - FORCED_FULL_PASS_FLOOR + timedelta(seconds=1)
    assert derivation_due(_SIGNATURE, _SIGNATURE, last, _NOW) is False
    assert derivation_due(_SIGNATURE, _SIGNATURE, _NOW - FORCED_FULL_PASS_FLOOR, _NOW) is True


def test_a_changed_signature_runs_a_pass_inside_the_floor() -> None:
    changed = DerivationSignature(segment_count=4, max_segment_id=10, max_received_at=_NOW, chunk_count=2)
    assert derivation_due(_SIGNATURE, changed, _NOW, _NOW) is True


# --- stamping ----------------------------------------------------------------------------------


def test_each_event_carries_its_segments_node_step_and_graph() -> None:
    current = SegmentDerivationInput(
        segment_id="sg_1",
        chunk_id="ch_1",
        node_id="g1-build",
        epoch=3,
        spawn_generation=1,
        normalizer_version="n1",
        turns=[],
        complete=True,
        content_fingerprint="fp",
        provenance=SegmentProvenance(harness_id=None, harness_version=None, model=None, effort=None),
    )
    extracted = ExtractedEvent(
        kind="file-read",
        turn_path="0",
        occurrence=0,
        payload={"b": 1, "a": 2},
        subject="a.py",
        tool="Read",
        depth=0,
        agent_type=None,
        occurred_at=_NOW,
    )
    [event] = stamp_events([extracted], current, "g1")
    assert (event.chunk_id, event.node_id, event.epoch, event.spawn_generation, event.graph_id) == (
        "ch_1",
        "g1-build",
        3,
        1,
        "g1",
    )
    assert event.payload == '{"a": 2, "b": 1}'
    assert (event.kind, event.subject, event.tool, event.occurred_at) == ("file-read", "a.py", "Read", _NOW)


# --- candidacy, completeness, version ----------------------------------------------------------


@pytest.mark.parametrize(
    ("marker", "current", "candidate"), [(None, "fp", True), ("old", "fp", True), ("fp", "fp", False)]
)
def test_a_segment_is_a_candidate_unless_its_marker_matches_its_content(
    marker: str | None, current: str, candidate: bool
) -> None:
    assert is_candidate(marker, current) is candidate


def test_a_segment_is_complete_unless_a_record_was_rejected() -> None:
    assert segment_complete([]) is True
    assert segment_complete([False, False]) is True
    assert segment_complete([False, True]) is False


def test_a_read_covers_the_version_it_names_else_the_current_one() -> None:
    assert read_version(None) == EXTRACTOR_VERSION
    assert read_version("blizzard-analytics/1") == "blizzard-analytics/1"
