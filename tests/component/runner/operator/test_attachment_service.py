"""``AttachmentService.attach`` over a real store: the order of the lifecycle pre-check, the hub's stage-time
format check, and the write — and the check's fail-open policy."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.leases.model import Lease
from blizzard.runner.leases.worker_lease import WorkerLease
from blizzard.runner.operator.attachments import (
    AttachmentEmpty,
    AttachmentFormatRefused,
    AttachmentOnClosedLease,
    AttachmentService,
)
from tests.support.runner_fakes import FakeArtifactChecker, SqlAlchemyRunnerStore, make_store

_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
_LEASE = Lease(
    lease_id="lease_1",
    chunk_id="ch_1",
    graph_id="gr_1",
    node_id="nd_build",
    node_name="build",
    epoch=1,
    retries_max=2,
    created_at=_NOW,
)


def _service(tmp_path: Path, checker: FakeArtifactChecker) -> tuple[AttachmentService, SqlAlchemyRunnerStore]:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    return AttachmentService(store, FixedClock(_NOW), store, store, stage_check=checker), store


def test_a_passing_check_stages_the_content(tmp_path: Path) -> None:
    checker = FakeArtifactChecker()
    service, store = _service(tmp_path, checker)
    service.attach(WorkerLease(lease=_LEASE, active=True), name="delta", content="body")
    assert checker.calls == [("ch_1", "delta", "body")]
    assert store.attachments_for_lease("lease_1") == {"delta": "body"}


def test_a_lifecycle_refusal_makes_no_hub_call(tmp_path: Path) -> None:
    checker = FakeArtifactChecker(refusal="never asked")
    service, store = _service(tmp_path, checker)
    with pytest.raises(AttachmentEmpty):
        service.attach(WorkerLease(lease=_LEASE, active=True), name="delta", content="")
    with pytest.raises(AttachmentOnClosedLease):
        service.attach(WorkerLease(lease=_LEASE, active=False), name="delta", content="body")
    assert checker.calls == []
    assert store.attachments_for_lease("lease_1") == {}


def test_a_hub_refusal_raises_and_writes_nothing(tmp_path: Path) -> None:
    checker = FakeArtifactChecker(refusal="does not match the shape")
    service, store = _service(tmp_path, checker)
    with pytest.raises(AttachmentFormatRefused, match="does not match the shape"):
        service.attach(WorkerLease(lease=_LEASE, active=True), name="delta", content="body")
    assert store.attachments_for_lease("lease_1") == {}


def test_no_verdict_stages_the_content_unchecked(tmp_path: Path) -> None:
    checker = FakeArtifactChecker(down=True)
    service, store = _service(tmp_path, checker)
    service.attach(WorkerLease(lease=_LEASE, active=True), name="delta", content="body")
    assert len(checker.calls) == 1
    assert store.attachments_for_lease("lease_1") == {"delta": "body"}
