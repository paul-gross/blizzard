"""CONTEXT SAMPLE — sample every running lease's live session context and warn on the first crossing."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogKind
from blizzard.foundation.fact_kinds import EVENT_RECORDED
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.environments.repository import (
    EnvBinding,
    IReadEnvironmentRepository,
    group_bindings_by_chunk,
)
from blizzard.runner.environments.workspaces import IWorkspaceSpawnRoots
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.registry import UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.harness.transcript import IHarnessTranscriptSource
from blizzard.runner.leases.model import Lease
from blizzard.runner.leases.record import IReadLeaseRecordRepository
from blizzard.runner.usage.repository import ContextSampleState, IWriteUsageRepository

_log = get_logger("blizzard.runner.loop")

# The crossing rides `event.recorded`, not a fact kind of its own — both hubs already ingest that
# lane. `(severity, kind)` per `src/blizzard/runner/lifecycle/attempt.py`; the kind is the EVENT's, never a fact's.
_CONTEXT_WARNED: EventLogKind = "worker-context-warned"


class ContextSampleConfig(Protocol):
    @property
    def context_warn_tokens(self) -> int | None: ...
    @property
    def context_sample_interval_seconds(self) -> int: ...


class ContextSampleStores(Protocol):
    @property
    def lease_record(self) -> IReadLeaseRecordRepository: ...
    @property
    def usage(self) -> IWriteUsageRepository: ...
    @property
    def environments(self) -> IReadEnvironmentRepository: ...


class ContextSampleContext(Protocol):
    @property
    def config(self) -> ContextSampleConfig: ...
    @property
    def stores(self) -> ContextSampleStores: ...
    @property
    def transcripts_wired(self) -> bool: ...
    @property
    def workspaces(self) -> IWorkspaceSpawnRoots: ...
    @property
    def clock(self) -> IClock: ...
    @property
    def events(self) -> IRunnerEventPublisher | None: ...
    def transcript_source_for(self, session: SessionReference) -> IHarnessTranscriptSource: ...


@dataclass(frozen=True)
class ContextSampleSweep:
    """A graph's `rotate` bounds are evaluated at SPAWN time, leaving a long invocation's inside
    unobserved — where a runaway session spends. This closes that gap, not the enforcement one."""

    ctx: ContextSampleContext

    def run(self) -> None:
        """Sample each active lease's context, warning the first time one crosses.

        No configured line means no transcript reads at all, so a runner that never opts in
        pays nothing. Per-lease failures are isolated: one unreadable transcript must not cost
        the other leases their samples."""
        ctx = self.ctx
        warn_tokens = ctx.config.context_warn_tokens
        if warn_tokens is None or not ctx.transcripts_wired:
            return
        try:
            leases = ctx.stores.lease_record.list_active_leases()
            # One bulk read for the whole active set (`bzh:bulk-reconstitution`); each
            # lease below filters "due" itself from its own already-fetched state.
            states = ctx.stores.usage.context_sample_states([lease.lease_id for lease in leases])
            bindings_by_chunk = group_bindings_by_chunk(ctx.stores.environments.held_bindings())
        except Exception as exc:  # a failed read skips this step only; the tick goes on
            _log.warning("context sample step failed", detail=str(exc))
            return
        for lease in leases:
            try:
                self._sample(lease, warn_tokens, states.get(lease.lease_id), bindings_by_chunk.get(lease.chunk_id, []))
            except Exception as exc:  # one lease's read must not end the sweep
                _log.warning("context sample failed", lease_id=lease.lease_id, detail=str(exc))

    def _sample(
        self,
        lease: Lease,
        warn_tokens: int,
        state: ContextSampleState | None,
        bindings: list[EnvBinding],
    ) -> None:
        ctx = self.ctx
        session = lease.session
        if session is None or not ctx.transcripts_wired:
            return  # a lease whose spawn has not yet minted a session has nothing to read
        now = ctx.clock.now()
        interval = timedelta(seconds=ctx.config.context_sample_interval_seconds)
        if not ContextSampleState.sample_due(state, now=now, interval=interval):
            return
        try:
            resolve = getattr(ctx, "lease_transcript_source", None)
            source = resolve(lease.lease_id, session) if resolve is not None else ctx.transcript_source_for(session)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            # A read-only side lane: never escalates on its own, only skips this one lease's
            # sample for this tick — its own dispatch path (a wake, a judgement) escalates it.
            _log.warning(
                "context sample skipped — owner unresolvable",
                lease_id=lease.lease_id,
                session_id=session.session_id,
                harness_id=session.harness_id,
                reason="unavailable" if isinstance(exc, UnavailableHarnessError) else "unknown",
                detail=str(exc),
            )
            return
        tokens = source.context_tokens(session.session_id, spawn_cwd=self._spawn_cwd(bindings))
        crossing = ContextSampleState.first_crossing(state, tokens=tokens, warn_tokens=warn_tokens)
        seq = ctx.stores.usage.record_context_sample(
            lease_id=lease.lease_id,
            chunk_id=lease.chunk_id,
            session=session,
            # `None` is *unmeasured*, recorded as an attempt so the cadence anchor still advances
            # — else an unreadable transcript is re-read every tick instead of every interval.
            context_tokens=tokens,
            sampled_at=now,
            report_kind=EVENT_RECORDED if crossing else "",
            report_payload=json.dumps(self._event(lease, tokens, warn_tokens, now)) if crossing else "",
        )
        if seq is not None and ctx.events is not None:
            ctx.events.publish_fact_changed(
                seq=seq,
                kind=EVENT_RECORDED,
                chunk_id=lease.chunk_id,
                lease_id=lease.lease_id,
            )

    @staticmethod
    def _event(lease: Lease, tokens: int | None, warn_tokens: int, now: datetime) -> dict[str, object]:
        """The ``event.recorded`` payload one crossing surfaces, in the shape the hub ingests."""
        return {
            "severity": EVENT_LOG_SEVERITY[_CONTEXT_WARNED],
            "kind": _CONTEXT_WARNED,
            "chunk_id": lease.chunk_id,
            "lease_id": lease.lease_id,
            "node_name": lease.node_name,
            "message": f"session context {tokens} tokens crossed the {warn_tokens} warn line",
            "detail": {
                "session_id": lease.session_id,
                "context_tokens": tokens,
                "warn_tokens": warn_tokens,
                "sampled_at": iso_utc(now),
            },
        }

    def _spawn_cwd(self, bindings: list[EnvBinding]) -> str | None:
        """The lease's worktree, the transcript locator's multi-match tie-break — never its key.

        Resolved exactly as the transcript pump resolves it, so both lanes read the same file."""
        binding = bindings[0] if bindings else None
        return SpawnCwd(
            self.ctx.workspaces.spawn_root(binding.workspace_id if binding else None),
            binding.workdir if binding else None,
        ).path
