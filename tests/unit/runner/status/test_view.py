"""The runner-local machine-status routes — ``GET /runner`` / ``/asks`` / ``/escalations``.

Exercised over a real store via TestClient. Hub-free but for the derived reachability
read: every route's shape, its empty and unwired forms, and the derivation->wire
mapping are the point."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Unpack

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.leases.model import NewLease
from blizzard.runner.status.view import EscalationView, RunnerStatusService
from tests.factory_fields import NewLeaseFields
from tests.runner_fakes import (
    FakeHarness,
    FakeProvider,
    SqlAlchemyRunnerStore,
    make_store,
    registered_identity,
    single_workspace,
)

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _seed_lease(store: SqlAlchemyRunnerStore, **overrides: Unpack[NewLeaseFields]) -> None:
    fields: NewLeaseFields = {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 1,
        "retries_max": 2,
        "created_at": _NOW,
    }
    fields.update(overrides)
    store.record_lease(NewLease(**fields))


@pytest.mark.parametrize("harness_id", [CLAUDE_CODE_HARNESS_ID, "unregistered"])
def test_an_escalation_composes_its_commands_only_under_a_registered_owner(tmp_path: Path, harness_id: str) -> None:
    """A registered owner composes the raw resume under the escalation's own stamps and its
    wrapped form; an owner this runner cannot resolve still lists the escalation, with neither."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    service = RunnerStatusService(
        FixedClock(_NOW),
        pause=store,
        daemon_liveness=store,
        lease_record=store,
        outbound=store,
        environments=store,
        asks=store,
        takeover=store,
        escalations=store,
        identity=registered_identity("rn_status"),
        runner_name="runner-local",
        workspace_id="workspace-local",
        hub_url="http://hub",
        workspaces=single_workspace(FakeProvider({})),
        runner_dir="/tmp/runner-dir",
        harnesses=HarnessRegistry(
            {
                CLAUDE_CODE_HARNESS_ID: HarnessBinding(
                    adapter=FakeHarness(
                        handle=WorkerHandle(session_id="sess-x", pid=1, process_start_time="start-1", pgid=1),
                        verdict=None,
                    )
                )
            }
        ),
    )
    _seed_lease(store, resolved_model="stamped-model", resolved_effort="high")
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(harness_id, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws1")
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="escalated", closed_at=_NOW)

    (escalation,) = service.escalations()

    assert escalation.harness_id == harness_id
    if harness_id == CLAUDE_CODE_HARNESS_ID:
        assert escalation.resume_command == "cd /ws/e1 && claude --resume sess-a --model stamped-model --effort high"
        assert (
            escalation.wrapped_takeover_command
            == "blizzard runner takeover ch_1 --dir /tmp/runner-dir --runner runner-local"
        )
    else:
        assert escalation.resume_command == ""
        assert escalation.wrapped_takeover_command is None


def test_an_escalation_view_carries_every_stamp_and_resumes_only_under_a_held_binding(tmp_path: Path) -> None:
    """One escalation with every stamp seeded, a registered owner and a workspace spawn root; one
    with a session but no held binding, which has nothing to resume in and so no command."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    service = RunnerStatusService(
        FixedClock(_NOW),
        pause=store,
        daemon_liveness=store,
        lease_record=store,
        outbound=store,
        environments=store,
        asks=store,
        takeover=store,
        escalations=store,
        identity=registered_identity("rn_status"),
        runner_name="runner-local",
        workspace_id="workspace-local",
        hub_url="http://hub",
        workspaces=single_workspace(FakeProvider({}), spawn_root="/ws"),
        runner_dir="/tmp/runner-dir",
        harnesses=HarnessRegistry(
            {
                CLAUDE_CODE_HARNESS_ID: HarnessBinding(
                    adapter=FakeHarness(
                        handle=WorkerHandle(session_id="sess-x", pid=1, process_start_time="start-1", pgid=1),
                        verdict=None,
                    )
                )
            }
        ),
    )
    for suffix in ("1", "2"):
        _seed_lease(
            store,
            lease_id=f"lease_{suffix}",
            chunk_id=f"ch_{suffix}",
            session_name="named-session",
            resolved_model="stamped-model",
            resolved_effort="high",
        )
        store.record_spawn(
            f"lease_{suffix}",
            pid=100,
            process_start_time="start-100",
            session=SessionReference(CLAUDE_CODE_HARNESS_ID, f"sess-{suffix}"),
            spawned_at=_NOW,
            harness_version="1.2.3",
        )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws1")
    closed_at = _NOW + timedelta(minutes=5)
    for suffix in ("1", "2"):
        store.record_closure(
            lease_id=f"lease_{suffix}",
            chunk_id=f"ch_{suffix}",
            node_id="nd_build",
            reason="escalated",
            closed_at=closed_at,
            escalation_cause="retries exhausted",
        )

    by_chunk = {e.chunk_id: e for e in service.escalations()}

    held = by_chunk["ch_1"]
    assert held == EscalationView(
        chunk_id="ch_1",
        lease_id="lease_1",
        node_id="nd_build",
        epoch=1,
        closed_at=closed_at,
        resume_command="cd /ws && claude --resume sess-1 --model stamped-model --effort high",
        session_name="named-session",
        model="stamped-model",
        effort="high",
        harness_id=CLAUDE_CODE_HARNESS_ID,
        harness_version="1.2.3",
        wrapped_takeover_command="blizzard runner takeover ch_1 --dir /tmp/runner-dir --runner runner-local",
        cause="retries exhausted",
    )
    unheld = by_chunk["ch_2"]
    assert unheld.resume_command == ""
    assert unheld.wrapped_takeover_command is None
    assert (unheld.session_name, unheld.model, unheld.effort) == ("named-session", "stamped-model", "high")
    assert unheld.cause == "retries exhausted"
