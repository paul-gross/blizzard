"""``TraceReplay`` — the window's bounds and its batching, told from an in-memory closing-steps double."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.hub.domain.observability.tracing.facts import StepFacts, TracedTransition
from blizzard.hub.domain.observability.tracing.replay import ReplayUnavailable, ReplayWindowRefused, TraceReplay
from blizzard.hub.domain.observability.tracing.repository import ClosingCandidates, IReadTraceSteps
from tests.support import InMemoryTraceExporter
from tests.trace_fixtures import make_facts, runner_epoch

_T0 = datetime(2026, 7, 13, tzinfo=UTC)
_CONFIG = TracingConfig(settle_seconds=0, sweep_seconds=60, max_lag_seconds=3600, replay_max_window=3600)


@pytest.mark.parametrize(
    ("since", "until", "named"),
    [
        (_T0, _T0, "until must be after since"),
        (_T0, _T0 - timedelta(seconds=1), "until must be after since"),
        (_T0, _T0 + timedelta(seconds=3601), "replay_max_window (3600 seconds)"),
    ],
)
def test_a_bad_window_is_refused_naming_its_bound(since: datetime, until: datetime, named: str) -> None:
    replay = TraceReplay(
        steps=cast(IReadTraceSteps, object()), exporter=None, clock=FixedClock(_T0 + timedelta(days=1)), config=_CONFIG
    )
    with pytest.raises(ReplayWindowRefused, match=named.replace("(", r"\(").replace(")", r"\)")):
        replay.replay(since, until, dry_run=True)


def test_a_wet_replay_with_no_exporter_is_unavailable() -> None:
    replay = TraceReplay(
        steps=cast(IReadTraceSteps, object()), exporter=None, clock=FixedClock(_T0 + timedelta(days=1)), config=_CONFIG
    )
    with pytest.raises(ReplayUnavailable):
        replay.replay(_T0, _T0 + timedelta(seconds=1), dry_run=False)


class _ClosingSteps:
    def __init__(self, *closings: tuple[str, datetime]) -> None:
        self._facts = {
            chunk_id: make_facts(
                chunk_id=chunk_id,
                **runner_epoch(1, 10),
                transitions=(TracedTransition(epoch=1, recorded_at=when, graph_id="g1", to_node_id="g1-review"),),
            )
            for chunk_id, when in closings
        }
        self._at = dict(closings)

    def snapshot(self) -> nullcontext[_ClosingSteps]:
        return nullcontext(self)

    def closing_candidates(self, since: datetime, until: datetime, limit: int) -> ClosingCandidates:
        inside = sorted((w, c) for c, w in self._at.items() if since <= w <= until)
        read = inside[:limit]
        frontier = inside[limit][0] if len(inside) > limit else None
        return ClosingCandidates(tuple(c for _, c in read), frontier, read[-1][0] if read else None)

    def step_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, StepFacts]:
        return {c: self._facts[c] for c in chunk_ids}

    def newest_cursor(self) -> None:
        return None

    def newest_export_latch(self) -> None:
        return None


def _unit_replay(steps: _ClosingSteps, config: TracingConfig) -> tuple[TraceReplay, InMemoryTraceExporter]:
    exporter = InMemoryTraceExporter()
    clock = FixedClock(_T0 + timedelta(days=1))
    return TraceReplay(steps=cast(IReadTraceSteps, steps), exporter=exporter, clock=clock, config=config), exporter


def test_a_replay_across_batches_counts_every_step_span_and_batch() -> None:
    steps = _ClosingSteps(*((f"ch_{n}", _T0 + timedelta(seconds=n)) for n in range(1, 6)))
    replay, exporter = _unit_replay(steps, TracingConfig(settle_seconds=0, batch_limit=2, replay_max_window=3600))

    result = replay.replay(_T0, _T0 + timedelta(seconds=10), dry_run=False)

    assert (result.steps, result.batches, result.chunks, result.failed) == (5, 3, 0, False)
    assert [len({s.context.trace_id for s in batch}) for batch in exporter.batches] == [2, 2, 1]
    assert [len(batch) for batch in exporter.batches] == [2, 2, 1]
    assert result.spans == 5
    dry = replay.replay(_T0, _T0 + timedelta(seconds=10), dry_run=True)
    assert (dry.steps, dry.spans, dry.batches, dry.dry_run) == (5, 5, 3, True)


def test_a_window_exactly_replay_max_window_wide_is_accepted() -> None:
    steps = _ClosingSteps(("ch_1", _T0 + timedelta(seconds=5)))
    replay, _exporter = _unit_replay(steps, TracingConfig(settle_seconds=0, replay_max_window=3600))

    assert replay.replay(_T0, _T0 + timedelta(seconds=3600), dry_run=True).steps == 1


def test_the_window_is_half_open_at_until() -> None:
    until = _T0 + timedelta(seconds=30)
    steps = _ClosingSteps(("ch_in", until - timedelta(microseconds=1)), ("ch_edge", until))
    replay, _exporter = _unit_replay(steps, TracingConfig(settle_seconds=0, replay_max_window=3600))

    result = replay.replay(_T0, until, dry_run=True)

    assert (result.steps, result.spans) == (1, 1)
