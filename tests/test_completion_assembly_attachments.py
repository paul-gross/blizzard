"""Completion assembly consumes durable attachments (issue #113, Phase 3).

Component tier: a real ``blizzard runner attach`` round trip — the actual FastAPI app,
lease-token authorization, and the durable store — feeds a real ADVANCE tick
(:func:`~blizzard.runner.loop.steps.advance`) so the assembled completion is proven
against real internal collaborators end to end, not just the pure
``_collect_asset_artifacts`` unit coverage in ``tests/test_runner_loop.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.hub.domain.enrollment import hash_token
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.domain.attachments import AttachmentService
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.steps import advance, pull
from blizzard.runner.store.repository import NewLease
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeWorktreeGit,
    make_context,
    make_envelope,
    make_store,
)

_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)
_TOKEN = "the-lease-token"
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


@pytest.mark.component
def test_advance_prefers_a_real_attachment_and_falls_back_for_the_rest(tmp_path: Path) -> None:
    """A multi-asset node: one `produces` name attached via the real runner API, the
    other not — the assembled completion must carry each distinctly (#90), not alias
    both to the shared assessment."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    attachments = AttachmentService(store, FixedClock(_NOW))
    app = create_app(config, runner_store=store, attachments=attachments)

    store.record_lease(
        NewLease(
            lease_id="lease_r",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_review",
            node_name="review",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_lease_token("lease_r", hash_token(_TOKEN), _NOW)
    store.record_spawn("lease_r", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)

    # The real attach round trip: the worker submits its findings through the runner's
    # own local HTTP API, authorized by its lease token — the one name it explicitly
    # attaches. `review-diary` is left un-attached to prove the fallback still fires.
    with TestClient(app) as client:
        resp = client.post(
            "/api/leases/lease_r/attachments",
            json={"name": "review-findings", "content": "the real findings"},
            headers={"X-Blizzard-Lease-Token": _TOKEN},
        )
    assert resp.status_code == 200, resp.text

    hub = FakeHub()
    hub.envelopes["ch_1"] = make_envelope(
        "ch_1",
        "review",
        node_id="nd_review",
        choices=_CHOICES,
        produces=["review-findings", "review-diary"],
    )
    hub.apply_responses = [ApplyResponse(outcome=ApplyOutcome.DONE)]
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100"),
        verdict="fail",
        assessment="the shared assessment",
    )
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        worktree_git=FakeWorktreeGit([]),
    )

    advance(ctx)  # assembles the completion from the real durable attachment + fallback
    pull(ctx)  # the flusher delivers it to the hub

    _, submission = hub.completions[0]
    by_name = {a.name: a for a in submission.artifacts}

    assert by_name["review-findings"].kind is ArtifactKind.ASSET
    assert by_name["review-findings"].content == "the real findings"
    assert by_name["review-findings"].attached is True

    assert by_name["review-diary"].kind is ArtifactKind.ASSET
    assert by_name["review-diary"].content == "the shared assessment"
    assert by_name["review-diary"].attached is False

    # The exact #90 aliasing bug: the two produced names must not read back the same
    # content just because only one was ever explicitly attached.
    assert by_name["review-findings"].content != by_name["review-diary"].content
