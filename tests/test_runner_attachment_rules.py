"""The attach rule, pinned by value: which attachments are staged and which are refused."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.runner.leases import Lease, WorkerLease
from blizzard.runner.operator.attachments import AttachmentEmpty, AttachmentOnClosedLease, check_attachable

pytestmark = pytest.mark.unit

_AT = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
_LEASE = Lease(
    lease_id="lease_1",
    chunk_id="ch_1",
    graph_id="gr_1",
    node_id="nd_build",
    node_name="build",
    epoch=1,
    runner_id="r1",
    retries_max=2,
    created_at=_AT,
)


def test_content_on_the_active_lease_is_staged() -> None:
    """Even after the worker exited with its judgement still in flight — latest wins."""
    check_attachable(WorkerLease(lease=_LEASE, active=True), name="review", content="looks good")


def test_empty_content_is_refused() -> None:
    with pytest.raises(AttachmentEmpty, match="'review' is empty"):
        check_attachable(WorkerLease(lease=_LEASE, active=True), name="review", content="")


def test_an_attach_on_a_takeovers_closed_reference_lease_is_refused() -> None:
    with pytest.raises(AttachmentOnClosedLease, match="lease_1 is closed"):
        check_attachable(WorkerLease(lease=_LEASE, active=False), name="review", content="c")


def test_empty_content_is_refused_first() -> None:
    with pytest.raises(AttachmentEmpty):
        check_attachable(WorkerLease(lease=_LEASE, active=False), name="review", content="")
