"""Fleet-summary bucket fold (unit tier) — ``derive_fleet_summary`` over derived statuses.

The runner machine panel's counts strip shows a fleet-level pulse: ready / running /
waiting / needs (issue #76). The fold from each chunk's derived status to those four
buckets is a pure function of the statuses (``bzh:domain-takes-objects``), so these tests
feed statuses directly — no store, no tokens. They pin the fold the wire model and the
strip both mirror: ``running`` unions ``delivering``, ``waiting`` unions ``paused``, and
the resting/terminal statuses (``not_ready``/``stopped``/``done``) count toward no bucket.
"""

from __future__ import annotations

import pytest

from blizzard.hub.domain.work import ChunkStatus, FleetSummary, derive_fleet_summary

pytestmark = pytest.mark.unit


def test_empty_fleet_is_all_zeros() -> None:
    assert derive_fleet_summary([]) == FleetSummary(ready=0, running=0, waiting=0, needs=0)


def test_each_status_lands_in_its_bucket() -> None:
    summary = derive_fleet_summary(
        [
            ChunkStatus.READY,
            ChunkStatus.RUNNING,
            ChunkStatus.WAITING_ON_HUMAN,
            ChunkStatus.NEEDS_HUMAN,
        ]
    )
    assert summary == FleetSummary(ready=1, running=1, waiting=1, needs=1)


def test_delivering_folds_into_running() -> None:
    # Live work in either shape — mid-node or entering the hub delivery node — is one
    # ``running`` pulse to the operator.
    assert derive_fleet_summary([ChunkStatus.RUNNING, ChunkStatus.DELIVERING]).running == 2


def test_paused_folds_into_waiting() -> None:
    # A paused chunk and a human-gated one both read as "waiting" on the strip.
    summary = derive_fleet_summary([ChunkStatus.PAUSED, ChunkStatus.WAITING_ON_HUMAN])
    assert summary.waiting == 2 and summary.running == 0


def test_resting_and_terminal_statuses_count_toward_no_bucket() -> None:
    # The strip is a live-work pulse, not a fleet total: not_ready / stopped / done are
    # deliberately uncounted, so a fleet of only these folds to all-zeros.
    summary = derive_fleet_summary([ChunkStatus.NOT_READY, ChunkStatus.STOPPED, ChunkStatus.DONE])
    assert summary == FleetSummary(ready=0, running=0, waiting=0, needs=0)


def test_counts_accumulate_across_a_mixed_fleet() -> None:
    summary = derive_fleet_summary(
        [
            ChunkStatus.READY,
            ChunkStatus.READY,
            ChunkStatus.RUNNING,
            ChunkStatus.DELIVERING,
            ChunkStatus.PAUSED,
            ChunkStatus.NEEDS_HUMAN,
            ChunkStatus.DONE,  # uncounted
        ]
    )
    assert summary == FleetSummary(ready=2, running=2, waiting=1, needs=1)
