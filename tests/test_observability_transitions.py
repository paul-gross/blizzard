"""The declared verb-by-state tables of the observability lanes — fact egress, trace export, and
event derivation — pinned by value."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.observability.analytics.events import (
    DERIVATION_TRANSITIONS,
    DerivationAction,
    DerivationVerb,
    MarkerState,
    is_candidate,
    marker_state,
)
from blizzard.hub.domain.observability.egress.lifecycle import (
    EGRESS_TRANSITIONS,
    ExportState,
    ExportVerb,
    export_allows,
)
from blizzard.hub.domain.observability.tracing.lifecycle import (
    TRACE_EXPORT_TRANSITIONS,
    TraceExportState,
    TraceVerb,
    trace_export_allows,
)

pytestmark = pytest.mark.unit


def test_the_egress_table_covers_every_state() -> None:
    assert set(EGRESS_TRANSITIONS) == set(ExportState)
    assert EGRESS_TRANSITIONS[ExportState.UNANCHORED] == EGRESS_TRANSITIONS[ExportState.ANCHORED] == set(ExportVerb)


@pytest.mark.parametrize(
    ("verb", "while_off"),
    [
        (ExportVerb.SWEEP, False),
        (ExportVerb.RESET, False),
        (ExportVerb.BACKFILL, False),
        (ExportVerb.DRY_BACKFILL, True),
        (ExportVerb.STATUS, True),
    ],
)
def test_only_a_dry_backfill_and_the_status_read_run_while_the_export_is_off(verb: ExportVerb, while_off: bool) -> None:
    assert export_allows(verb, wired=False) is while_off
    assert export_allows(verb, wired=True) is True


@pytest.mark.parametrize(
    ("verb", "while_off"),
    [(TraceVerb.SWEEP, False), (TraceVerb.REPLAY, False), (TraceVerb.DRY_REPLAY, True), (TraceVerb.STATUS, True)],
)
def test_only_a_dry_replay_and_the_status_read_run_while_tracing_is_off(verb: TraceVerb, while_off: bool) -> None:
    assert set(TRACE_EXPORT_TRANSITIONS) == set(TraceExportState)
    assert trace_export_allows(verb, exporter_wired=False) is while_off
    assert trace_export_allows(verb, exporter_wired=True) is True


@pytest.mark.parametrize(
    ("marker", "current", "state"),
    [
        ("fp", None, MarkerState.GONE),
        (None, None, MarkerState.GONE),
        (None, "fp", MarkerState.NONE),
        ("fp", "fp", MarkerState.CURRENT),
        ("old", "fp", MarkerState.STALE),
    ],
)
def test_a_segments_marker_state(marker: str | None, current: str | None, state: MarkerState) -> None:
    assert marker_state(marker, current) is state


def test_the_derivation_table() -> None:
    derive, skip, drop = DerivationAction.DERIVE, DerivationAction.SKIP, DerivationAction.DROP
    rows = {state: tuple(DERIVATION_TRANSITIONS[state][verb] for verb in DerivationVerb) for state in MarkerState}
    assert rows == {
        MarkerState.NONE: (derive, derive, derive),
        MarkerState.CURRENT: (skip, derive, skip),
        MarkerState.STALE: (derive, derive, derive),
        MarkerState.GONE: (drop, skip, skip),
    }
    assert [is_candidate(m, "fp") for m in (None, "fp", "old")] == [True, False, True]
