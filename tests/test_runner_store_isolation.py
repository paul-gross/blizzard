"""One runner's store adapters never read or change another runner's rows (component tier).

A migrated-to-head store is seeded for runner A through its own write Protocols (:func:`build_runner_world`), then every
runner-scoped row is copied as runner B's — the same shapes, the same chunk ids, only the keys a table holds unique
renamed. Through A's adapters:

- every read in ``RUNNER_CENSUS`` answers exactly what it answered before B's rows existed — so no B row is returned,
  the ``src/blizzard/runner/store/internal/base.py`` predicates (held bindings, open intents, pause parks, escalations) included;
- every write in :data:`WRITE_CENSUS` leaves each of B's rows as it was.

Coverage is held by reflection: every runner ``IWrite*`` Protocol method is in :data:`WRITE_CENSUS` or
:data:`WRITE_EXEMPTIONS`, as the read-index gate holds the read census."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import shutil
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

import blizzard.runner as runner_pkg
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.auth.tokens import IWriteTokenRepository
from blizzard.runner.composition import build_identity_forgetter
from blizzard.runner.environments.repository import BindingCompletion, IWriteEnvironmentRepository
from blizzard.runner.harness.fingerprint import PreambleFingerprint
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.selftest_result import IWriteSelfTestResultRepository
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.harness.workspace_prompts import IWriteWorkspacePromptRepository
from blizzard.runner.hub.host_identity import IWriteHostIdentityRepository
from blizzard.runner.hub.identity import IWriteRetiredRunnerIdentity, IWriteRunnerIdentityRepository, RunnerIdentity
from blizzard.runner.hub.outbound_buffer import IWriteOutboundRepository
from blizzard.runner.hub.served_projects import IWriteServedProjectsRepository, ServedProject
from blizzard.runner.leases.asks import IWriteAskRepository
from blizzard.runner.leases.elicitation import IWriteElicitationRepository
from blizzard.runner.leases.escalations import IWriteEscalationRepository
from blizzard.runner.leases.liveness import IWriteLeaseLivenessRepository
from blizzard.runner.leases.model import NewLease
from blizzard.runner.leases.operator_requests import IWriteAttachmentRepository, IWriteRequeueRepository
from blizzard.runner.leases.overload import IWriteOverloadRepository
from blizzard.runner.leases.record import IWriteLeaseRecordRepository
from blizzard.runner.leases.resume_intent import IWriteLeaseResumeIntentRepository
from blizzard.runner.leases.session import IWriteLeaseSessionRepository
from blizzard.runner.lifecycle.artifacts import IWriteGraphArtifactRepository
from blizzard.runner.lifecycle.daemon_liveness import IWriteDaemonLivenessRepository
from blizzard.runner.lifecycle.judgement.checks import ExecutedCheck, IWriteCheckRepository
from blizzard.runner.lifecycle.judgement.git_commit_declaration import IWriteGitCommitDeclarationRepository
from blizzard.runner.lifecycle.takeover import IWriteTakeoverRepository
from blizzard.runner.store import schema
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.store.scoped import RUNNER_SCOPING, RUNNER_UNSCOPED_OPTION
from blizzard.runner.throttle.pause import IWritePauseRepository
from blizzard.runner.tracing.cursor import LeaseCursorKey
from blizzard.runner.tracing.repository import IWriteLeaseTraces, LeaseTraceCheckpoint
from blizzard.runner.transcripts.invocation_boundaries import IWriteInvocationBoundaryRepository
from blizzard.runner.transcripts.ledger import IWriteTranscriptLedgerRepository
from blizzard.runner.transfer.rows import IWriteHostIngestRepository
from blizzard.runner.usage.repository import (
    IWriteCredentialRenewalRepository,
    IWriteExternalUsageRepository,
    IWriteHostLaneRepository,
    IWriteUsageRepository,
)
from tests.runner_fakes import record_usage, runner_migration_prototype
from tests.store_read_census import GRAPH_ID, RUNNER_CENSUS, RunnerWorld, build_runner_world

pytestmark = pytest.mark.component

RUNNER_A = "rn_a"
RUNNER_B = "rn_b"

#: Columns a runner-scoped table holds unique (or that name such a key), renamed in B's copy so it can sit beside A's.
_RENAMED_KEYS = frozenset({"lease_id", "segment_id", "takeover_id", "supersedes"})
_RUNNER_TABLES = [table for table in schema.metadata.sorted_tables if RUNNER_SCOPING.is_keyed(table)]
_UNSCOPED = {RUNNER_UNSCOPED_OPTION: True}


def _b_row(table: sa.Table, row: dict[str, Any]) -> dict[str, Any]:
    """A's ``row`` of ``table`` as runner B's: its surrogate key left to the store, its unique keys renamed."""
    copied: dict[str, Any] = {}
    for column in table.columns:
        value = row[column.name]
        if column.primary_key and column.name == "id":
            continue
        if column.name == "runner_id":
            value = RUNNER_B
        elif value is not None and (
            column.name in _RENAMED_KEYS or (table.name == "route_tokens" and column.primary_key)
        ):
            value = f"{RUNNER_B}:{value}"
        elif table.name == "hub_served_projects" and column.name == "position" and value is not None:
            value = value + 1000
        copied[column.name] = value
    return copied


def _copy_as_runner_b(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        for table in _RUNNER_TABLES:
            rows = conn.execute(
                sa.select(table).where(table.c.runner_id == RUNNER_A).execution_options(**_UNSCOPED)
            ).mappings()
            copies = [_b_row(table, dict(row)) for row in rows]
            if copies:
                conn.execute(sa.insert(table).execution_options(**_UNSCOPED), copies)


def _rows_of(engine: sa.Engine, runner_id: str) -> dict[str, list[tuple[Any, ...]]]:
    """Every runner-scoped row ``runner_id`` holds, per table, in a stable order."""
    with engine.connect() as conn:
        return {
            table.name: sorted(
                (
                    tuple(row)
                    for row in conn.execute(
                        sa.select(table).where(table.c.runner_id == runner_id).execution_options(**_UNSCOPED)
                    )
                ),
                key=repr,
            )
            for table in _RUNNER_TABLES
        }


def _answer(result: object) -> str:
    if isinstance(result, Iterator):
        result = list(result)
    return repr(result)


@pytest.fixture
def world(tmp_path: Path) -> Iterator[RunnerWorld]:
    db_path = tmp_path / "runner.db"
    shutil.copyfile(runner_migration_prototype(), db_path)
    engine = create_engine_from_url(f"sqlite:///{db_path}")
    try:
        yield build_runner_world(engine, RUNNER_A)
    finally:
        engine.dispose()


def test_runner_a_reads_none_of_runner_b_rows(world: RunnerWorld) -> None:
    alone = {key: _answer(recipe(world)) for key, recipe in RUNNER_CENSUS.items()}
    _copy_as_runner_b(world.engine)
    a_rows, b_rows = _rows_of(world.engine, RUNNER_A), _rows_of(world.engine, RUNNER_B)
    assert {t for t, rows in b_rows.items() if rows} == {t for t, rows in a_rows.items() if rows}

    beside_b = {key: _answer(recipe(world)) for key, recipe in RUNNER_CENSUS.items()}

    changed = [
        f"{protocol.__name__}.{method}"
        for (protocol, method) in alone
        if alone[(protocol, method)] != beside_b[(protocol, method)]
    ]
    assert not changed, (
        f"these reads through runner A's adapters answered differently beside runner B's rows: {changed}"
    )


# --- writes ------------------------------------------------------------------------------------------------------------

_SESSION = SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-1")
_LEASE = "lease_w"
_WRITES_FROM = datetime(2026, 9, 15, tzinfo=UTC)
WriteRecipe = Callable[[RunnerWorld], object]


def _at(seconds: float) -> datetime:
    """A stamp after every seeded one."""
    return _WRITES_FROM + timedelta(seconds=seconds)


def _new_lease(w: RunnerWorld) -> None:
    w.stores.lease_record.record_lease(
        NewLease(
            lease_id=_LEASE,
            chunk_id=w.chunk_1,
            graph_id=GRAPH_ID,
            node_id=w.node_a,
            node_name="build",
            epoch=9,
            retries_max=3,
            created_at=_at(0),
            session_name="pool-a",
        )
    )


def _segment(w: RunnerWorld) -> str:
    return w.stores.transcript_ledger.open_transcript_segment(
        chunk_id=w.chunk_1,
        node_id=w.node_a,
        epoch=9,
        generation=1,
        lease_id=w.lease_2,
        stamped_at=_at(1),
        session=_SESSION,
    )


def _sample() -> UsageSample:
    return UsageSample(
        kind="resume",
        model="model-b",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=0.01,
    )


#: Every runner ``IWrite*`` method a runner-scoped row can be written or changed by, driven through runner A's adapters
#: against the seeded world — on A's own chunks and leases, so an unconfined write would reach B's same-shaped rows.


def _engage_under_lock(w: RunnerWorld) -> None:
    """One locked brake transaction over the world's runner alone: its brake read and its fact written on the handle."""
    with w.stores.pause.locked_brakes([]) as locked:
        w.stores.pause.local_brake_locked(locked)
        w.stores.pause.record_local_pause_locked(
            locked, paused=True, at=_at(53), by="usage-limit", report_kind="runner.locally_paused", report_payload="{}"
        )


WRITE_CENSUS: dict[tuple[type, str], WriteRecipe] = {
    (IWriteLeaseRecordRepository, "record_lease"): _new_lease,
    (IWriteLeaseRecordRepository, "record_closure"): lambda w: w.stores.lease_record.record_closure(
        lease_id=w.lease_2,
        chunk_id=w.chunk_1,
        node_id=w.node_a,
        reason="transitioned",
        closed_at=_at(2),
        event_kind="lease.closed",
        event_payload="{}",
    ),
    (IWriteLeaseLivenessRepository, "record_heartbeat"): lambda w: w.stores.liveness.record_heartbeat(
        lease_id=w.lease_2, beat_at=_at(3)
    ),
    (IWriteLeaseLivenessRepository, "prune_heartbeats"): lambda w: w.stores.liveness.prune_heartbeats(now=_at(10**7)),
    (IWriteLeaseLivenessRepository, "record_spawn"): lambda w: w.stores.liveness.record_spawn(
        w.lease_3, pid=301, process_start_time="st-301", spawned_at=_at(4), session=_SESSION
    ),
    (IWriteLeaseLivenessRepository, "record_provisional_spawn"): lambda w: w.stores.liveness.record_provisional_spawn(
        w.lease_5, pid=401, process_start_time="st-401", pgid=None, spawned_at=_at(5), harness_id=CLAUDE_CODE_HARNESS_ID
    ),
    (IWriteLeaseLivenessRepository, "record_identified_spawn"): lambda w: w.stores.liveness.record_identified_spawn(
        w.lease_5, session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-4"), identified_at=_at(6)
    ),
    (IWriteLeaseLivenessRepository, "record_identity_failed"): lambda w: w.stores.liveness.record_identity_failed(
        w.lease_7, at=_at(7)
    ),
    (IWriteLeaseResumeIntentRepository, "record_resume_intent"): lambda w: w.stores.resume_intent.record_resume_intent(
        lease_id=w.lease_2, marked_at=_at(8)
    ),
    (IWriteLeaseResumeIntentRepository, "record_resume_clear"): lambda w: w.stores.resume_intent.record_resume_clear(
        lease_id=w.lease_8, cleared_at=_at(9)
    ),
    (IWriteLeaseSessionRepository, "record_mint_owner"): lambda w: w.stores.session.record_mint_owner(
        w.lease_2, CLAUDE_CODE_HARNESS_ID
    ),
    (IWriteLeaseSessionRepository, "record_session_end"): lambda w: w.stores.session.record_session_end(
        lease_id=w.lease_2, ended_at=_at(10)
    ),
    (IWriteLeaseSessionRepository, "record_session_preamble"): lambda w: w.stores.session.record_session_preamble(
        _SESSION, fingerprint=PreambleFingerprint(blizzard="d1", workspace="d2"), at=_at(11)
    ),
    (IWriteEnvironmentRepository, "record_binding"): lambda w: w.stores.environments.record_binding(
        chunk_id=w.chunk_3, environment_id="env-1", workdir="/ws/env-1", bound_at=_at(12)
    ),
    (IWriteEnvironmentRepository, "complete_bindings"): lambda w: w.stores.environments.complete_bindings(
        [
            BindingCompletion(binding_id=b.binding_id, workspace_id="ws-a", project_id="pr-a")
            for b in w.stores.environments.incomplete_held_bindings()
        ]
    ),
    (IWriteEnvironmentRepository, "record_release"): lambda w: w.stores.environments.record_release(
        chunk_id=w.chunk_1, environment_id="env-1", released_at=_at(13)
    ),
    (IWriteTokenRepository, "set_route_token"): lambda w: w.stores.tokens.set_route_token(
        w.chunk_1, token="route-token-2", at=_at(14)
    ),
    (IWriteTokenRepository, "record_lease_token"): lambda w: w.stores.tokens.record_lease_token(
        w.lease_3, "hash-lease3", _at(15)
    ),
    (IWriteOutboundRepository, "enqueue_outbound"): lambda w: w.stores.outbound.enqueue_outbound(
        kind="lease.minted", chunk_id=w.chunk_1, lease_id=w.lease_2, payload="{}", created_at=_at(16)
    ),
    (IWriteOutboundRepository, "ack_outbound"): lambda w: w.stores.outbound.ack_outbound(
        w.read.outbound.pending_outbound(limit=1)[0].seq, acked_at=_at(17)
    ),
    (IWriteOutboundRepository, "ack_outbound_batch"): lambda w: w.stores.outbound.ack_outbound_batch(
        [entry.seq for entry in w.read.outbound.pending_outbound()], acked_at=_at(18)
    ),
    (IWriteOutboundRepository, "prune_outbound"): lambda w: w.stores.outbound.prune_outbound(now=_at(10**7)),
    (IWriteServedProjectsRepository, "record_served_projects"): lambda w: (
        w.stores.served_projects.record_served_projects(
            (ServedProject("other", "prj_2", "other", "Other"),), at=_at(19)
        )
    ),
    (IWriteRunnerIdentityRepository, "record_runner_identity"): lambda w: w.stores.identity.record_runner_identity(
        RunnerIdentity(RUNNER_A, "runner-renamed", _at(20))
    ),
    (IWriteAskRepository, "record_ask"): lambda w: w.stores.asks.record_ask(
        lease_id=w.lease_2, chunk_id=w.chunk_1, question_id="qn_w", question="?", options=[], asked_at=_at(21)
    ),
    (IWriteAskRepository, "record_park"): lambda w: w.stores.asks.record_park(
        lease_id=w.lease_2, chunk_id=w.chunk_1, question_id="qn_open1", parked_at=_at(22)
    ),
    (IWriteAskRepository, "record_park_resume"): lambda w: w.stores.asks.record_park_resume(
        lease_id=w.lease_3, question_id="qn_parked1", resumed_at=_at(23)
    ),
    (IWriteElicitationRepository, "record_elicitation_launch"): lambda w: (
        w.stores.elicitations.record_elicitation_launch(w.lease_2, 2, output_path="/tmp/elicit-w", at=_at(24))
    ),
    (IWriteElicitationRepository, "record_elicitation_started"): lambda w: (
        w.stores.elicitations.record_elicitation_started(w.lease_7, 1, pid=501, process_start_time="st-501")
    ),
    (IWriteElicitationRepository, "record_elicitation_relaunch"): lambda w: (
        w.stores.elicitations.record_elicitation_relaunch(w.lease_7, 1, output_path="/tmp/elicit-2")
    ),
    (IWriteElicitationRepository, "clear_elicitation"): lambda w: w.stores.elicitations.clear_elicitation(w.lease_7, 1),
    (IWriteEscalationRepository, "record_escalation_closure"): lambda w: w.stores.escalations.record_escalation_closure(
        chunk_id=w.chunk_3, reason="resolved", at=_at(25)
    ),
    (IWriteAttachmentRepository, "record_attachment"): lambda w: w.stores.attachments.record_attachment(
        lease_id=w.lease_2,
        chunk_id=w.chunk_1,
        node_id=w.node_a,
        epoch=2,
        name="asset-a",
        content="content-w",
        attached_at=_at(26),
    ),
    (IWriteRequeueRepository, "record_requeue"): lambda w: w.stores.requeue.record_requeue(
        chunk_id=w.chunk_1, at=_at(27)
    ),
    (IWriteOverloadRepository, "record_overload"): lambda w: w.stores.overload.record_overload(
        lease_id=w.lease_2,
        chunk_id=w.chunk_1,
        epoch=2,
        generation=2,
        invocation_kind="worker",
        invocation_identity="2",
        streak_ordinal=2,
        observed_at=_at(28),
        resume_after=_at(88),
    ),
    (IWriteOverloadRepository, "record_reset"): lambda w: w.stores.overload.record_reset(
        lease_id=w.lease_2, epoch=2, at=_at(29)
    ),
    (IWriteCheckRepository, "record_check_results"): lambda w: w.stores.checks.record_check_results(
        lease_id=w.lease_2,
        chunk_id=w.chunk_1,
        node_id=w.node_a,
        epoch=2,
        results=[ExecutedCheck(command="pytest", passed=False, output_tail="no")],
        at=_at(30),
    ),
    (IWriteCheckRepository, "record_checks_ran"): lambda w: w.stores.checks.record_checks_ran(
        lease_id=w.lease_2, epoch=2, at=_at(31)
    ),
    (IWriteCheckRepository, "record_nudge_fired"): lambda w: w.stores.checks.record_nudge_fired(
        lease_id=w.lease_2, epoch=2, at=_at(32)
    ),
    (IWriteGitCommitDeclarationRepository, "record_git_commit_declaration"): lambda w: (
        w.stores.git_commit_declarations.record_git_commit_declaration(
            lease_id=w.lease_2,
            chunk_id=w.chunk_1,
            node_id=w.node_a,
            epoch=2,
            environment_id="env-1",
            repo="repoA",
            branch="main",
            commit="fff000",
            declared_at=_at(33),
        )
    ),
    (IWriteTakeoverRepository, "record_takeover"): lambda w: w.stores.takeover.record_takeover(
        takeover_id="tko_w",
        chunk_id=w.chunk_2,
        lease_id=w.lease_3,
        workdir="/ws/env-2",
        fence_epoch=None,
        opened_at=_at(34),
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-2"),
    ),
    (IWriteTakeoverRepository, "record_takeover_end"): lambda w: w.stores.takeover.record_takeover_end(
        takeover_id="tko_1", ended_at=_at(35)
    ),
    (IWritePauseRepository, "set_hub_paused"): lambda w: w.stores.pause.set_hub_paused(paused=True, at=_at(36)),
    (IWritePauseRepository, "record_local_pause"): lambda w: w.stores.pause.record_local_pause(
        paused=True, at=_at(37), by="operator", report_kind="runner.locally_paused", report_payload="{}"
    ),
    (IWritePauseRepository, "locked_brakes"): lambda w: _engage_under_lock(w),
    (IWritePauseRepository, "local_brake_locked"): lambda w: _engage_under_lock(w),
    (IWritePauseRepository, "record_local_pause_locked"): lambda w: _engage_under_lock(w),
    (IWritePauseRepository, "record_pause_park"): lambda w: w.stores.pause.record_pause_park(
        lease_id=w.lease_2, chunk_id=w.chunk_1, parked_at=_at(38)
    ),
    (IWritePauseRepository, "record_pause_park_resume"): lambda w: w.stores.pause.record_pause_park_resume(
        lease_id=w.lease_8, resumed_at=_at(39)
    ),
    (IWriteLeaseTraces, "append_trace_cursor"): lambda w: w.stores.lease_traces.append_trace_cursor(
        LeaseTraceCheckpoint(LeaseCursorKey.opening(_at(40)), 3, _at(40))
    ),
    (IWriteLeaseTraces, "record_trace_latch"): lambda w: w.stores.lease_traces.record_trace_latch(
        "trace-export-failed", at=_at(41), report_kind="event.recorded", report_payload="{}"
    ),
    (IWriteInvocationBoundaryRepository, "record_boundary_open"): lambda w: (
        w.stores.invocation_boundaries.record_boundary_open(
            lease_id=w.lease_2,
            chunk_id=w.chunk_1,
            node_id=w.node_a,
            epoch=2,
            generation=3,
            kind="resume",
            start_position=None,
            opened_at=_at(42),
        )
    ),
    (IWriteInvocationBoundaryRepository, "record_boundary_advance"): lambda w: (
        w.stores.invocation_boundaries.record_boundary_advance(
            lease_id=w.lease_2,
            generation=3,
            kind="resume",
            superseded_invocation="el-2",
            start_position=None,
            advanced_at=_at(43),
        )
    ),
    (IWriteInvocationBoundaryRepository, "close_boundaries_for_lease"): lambda w: (
        w.stores.invocation_boundaries.close_boundaries_for_lease(w.lease_2, reason="closed", at=_at(44))
    ),
    (IWriteTranscriptLedgerRepository, "open_transcript_segment"): _segment,
    (IWriteTranscriptLedgerRepository, "record_transcript_deltas"): lambda w: (
        w.stores.transcript_ledger.record_transcript_deltas(
            segment_id=w.transcript_segment_id,
            chunk_id=w.chunk_1,
            cursor="cur-2",
            shipped_bytes=200,
            shipped_turns=3,
            normalizer_version="v1",
            harness_version="h1",
            payloads=['{"turn": 3}'],
            created_at=_at(45),
        )
    ),
    (IWriteTranscriptLedgerRepository, "advance_transcript_cursor"): lambda w: (
        w.stores.transcript_ledger.advance_transcript_cursor(
            w.transcript_segment_id, cursor="cur-3", normalizer_version="v1", harness_version="h1"
        )
    ),
    (IWriteTranscriptLedgerRepository, "mark_transcript_record_truncated"): lambda w: (
        w.stores.transcript_ledger.mark_transcript_record_truncated(w.transcript_segment_id, reason="big", severity=1)
    ),
    (IWriteTranscriptLedgerRepository, "mark_sidechain_dropped_warned"): lambda w: (
        w.stores.transcript_ledger.mark_sidechain_dropped_warned(w.transcript_segment_id, agent_id=None)
    ),
    (IWriteTranscriptLedgerRepository, "stop_transcript_segment_shipping"): lambda w: (
        w.stores.transcript_ledger.stop_transcript_segment_shipping(w.transcript_segment_id, reason="stopped")
    ),
    (IWriteTranscriptLedgerRepository, "finalize_transcript_segment"): lambda w: (
        w.stores.transcript_ledger.finalize_transcript_segment(w.transcript_segment_id, finalized_at=_at(46))
    ),
    (IWriteTranscriptLedgerRepository, "ack_transcript_outbound"): lambda w: (
        w.stores.transcript_ledger.ack_transcript_outbound(
            w.read.transcript_ledger.pending_transcript_outbound(limit=1)[0].seq, acked_at=_at(47)
        )
    ),
    (IWriteTranscriptLedgerRepository, "ack_transcript_outbound_batch"): lambda w: (
        w.stores.transcript_ledger.ack_transcript_outbound_batch(
            [entry.seq for entry in w.read.transcript_ledger.pending_transcript_outbound()], acked_at=_at(48)
        )
    ),
    (IWriteUsageRepository, "record_usage"): lambda w: record_usage(
        w.stores.usage,
        lease_id=w.lease_2,
        chunk_id=w.chunk_1,
        node_id=w.node_a,
        epoch=2,
        generation=3,
        sample=_sample(),
        recorded_at=_at(49),
    ),
    (IWriteUsageRepository, "record_context_sample"): lambda w: w.stores.usage.record_context_sample(
        lease_id=w.lease_2,
        chunk_id=w.chunk_1,
        context_tokens=900,
        sampled_at=_at(50),
        session=_SESSION,
        report_kind="context.sampled",
        report_payload="{}",
    ),
    (IWriteExternalUsageRepository, "record_external_usage_attempt"): lambda w: (
        w.host.external_usage.record_external_usage_attempt(
            slug=w.usage_slug, sampled_at=_at(51), payload="{}", report_kind="usage.sampled", report_payload="{}"
        )
    ),
    (IWriteSelfTestResultRepository, "record_selftest_result"): lambda w: (
        w.stores.selftest_results.record_selftest_result(
            harness_id=CLAUDE_CODE_HARNESS_ID, status="failed", error="boom", recorded_at=_at(52)
        )
    ),
    # Last: it deletes runner A's identity row, which no recipe above may depend on afterwards.
    (IWriteRetiredRunnerIdentity, "forget_runner_identity"): lambda w: build_identity_forgetter(
        w.engine, errors=RunnerStoreErrorFactory(get_logger("test")), runner_id=RUNNER_A
    ).forget_runner_identity(),
}

_HOST_ONLY = "writes only host-scoped tables, which carry no runner"

#: Runner ``IWrite*`` methods that write no runner-scoped row, each reasoned.
WRITE_EXEMPTIONS: dict[tuple[type, str], str] = {
    (IWriteDaemonLivenessRepository, "record_daemon_liveness"): _HOST_ONLY + " (daemon_liveness)",
    (IWriteGraphArtifactRepository, "record_graph_artifacts"): _HOST_ONLY + " (graph_artifacts)",
    (IWriteWorkspacePromptRepository, "set_workspace_prompt"): _HOST_ONLY + " (workspace_prompt)",
    (IWriteWorkspacePromptRepository, "clear_workspace_prompt"): _HOST_ONLY + " (workspace_prompt)",
    (IWriteExternalUsageRepository, "prune_external_usage_samples"): _HOST_ONLY + " (external_usage_samples)",
    (IWriteHostLaneRepository, "record_host_external_usage_attempt"): _HOST_ONLY
    + " (external_usage_samples, host_outbound_buffer)",
    (IWriteHostIdentityRepository, "record_host_identity"): _HOST_ONLY + " (host_identity)",
    (IWriteHostIngestRepository, "merge_host_rows_locked"): _HOST_ONLY + " (every carried host table)",
    (IWriteHostIngestRepository, "record_host_ingest_locked"): _HOST_ONLY + " (host_ingests)",
    (IWriteHostLaneRepository, "pending_host_facts"): _HOST_ONLY + " (host_outbound_buffer, read only)",
    (IWriteHostLaneRepository, "record_host_high_water"): _HOST_ONLY + " (host_high_water_seen, host_outbound_buffer)",
    (IWriteCredentialRenewalRepository, "claim_credential_renewal"): _HOST_ONLY + " (credential_renewal_claims)",
    (IWriteCredentialRenewalRepository, "record_credential_renewal_outcome"): _HOST_ONLY
    + " (credential_renewal_outcomes)",
    (IWriteCredentialRenewalRepository, "prune_credential_renewals"): _HOST_ONLY + " (credential_renewal_*)",
}


def _reflect_write_protocol_methods() -> set[tuple[type, str]]:
    """Every ``IWrite*`` Protocol under ``blizzard.runner``, keyed ``(Protocol, method)`` for each method its own body
    declares."""
    keys: set[tuple[type, str]] = set()
    for modinfo in pkgutil.walk_packages(runner_pkg.__path__, runner_pkg.__name__ + "."):
        if modinfo.name.endswith(".migrations.env") or ".migrations.tests" in modinfo.name:
            continue
        module = importlib.import_module(modinfo.name)
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if not name.startswith("IWrite") or obj.__module__ != module.__name__:
                continue
            if not getattr(obj, "_is_protocol", False):
                continue
            keys.update(
                (obj, member) for member, value in vars(obj).items() if not member.startswith("_") and callable(value)
            )
    return keys


def test_the_write_census_is_exhaustive() -> None:
    reflected = _reflect_write_protocol_methods()
    declared = set(WRITE_CENSUS) | set(WRITE_EXEMPTIONS)
    assert not set(WRITE_CENSUS) & set(WRITE_EXEMPTIONS)
    assert reflected == declared, (
        f"missing from WRITE_CENSUS/WRITE_EXEMPTIONS: {reflected - declared}; stale: {declared - reflected}"
    )


def test_runner_a_writes_change_none_of_runner_b_rows(world: RunnerWorld) -> None:
    _copy_as_runner_b(world.engine)
    b_before, a_before = _rows_of(world.engine, RUNNER_B), _rows_of(world.engine, RUNNER_A)

    for (protocol, method), recipe in WRITE_CENSUS.items():
        try:
            recipe(world)
        except Exception as exc:
            raise AssertionError(f"{protocol.__name__}.{method} raised: {exc!r}") from exc

    assert _rows_of(world.engine, RUNNER_B) == b_before
    assert _rows_of(world.engine, RUNNER_A) != a_before
