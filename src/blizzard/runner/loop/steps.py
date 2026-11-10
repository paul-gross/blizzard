"""The reconciliation steps — REAP → PULL → FILL → ADVANCE (``bzh:steppable-loop``).

Each is an individually runnable :class:`Step` over a :class:`LoopContext`. Every step is
idempotent and holds no state of its own — all facts live in the runner store, so a crash
mid-tick and a restart re-run the tick harmlessly; startup recovery is REAP running first.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogKind
from blizzard.foundation.fact_kinds import EVENT_RECORDED
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.environments.repository import (
    EnvBinding,
    group_bindings_by_chunk,
)
from blizzard.runner.harness.registry import UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.harness.spawn_cwd import SpawnCwd
from blizzard.runner.leases.model import Lease
from blizzard.runner.lifecycle.advance import AdvanceSweep
from blizzard.runner.lifecycle.claim import InterruptedClaims
from blizzard.runner.lifecycle.drain import OutboundDrain
from blizzard.runner.lifecycle.fill import BindingFill
from blizzard.runner.lifecycle.held_binding_completion import HeldBindingCompletion
from blizzard.runner.lifecycle.ownership_reconcile import OwnershipReconcile
from blizzard.runner.lifecycle.reap import ReapSweep
from blizzard.runner.lifecycle.registration import Registration
from blizzard.runner.lifecycle.restart_resume import RestartResume
from blizzard.runner.lifecycle.spawn import Spawner
from blizzard.runner.lifecycle.spend_ceiling import SpendCeilingCheck
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.throttle.gate import ThrottleGate
from blizzard.runner.throttle.levers import LeverAnswer
from blizzard.runner.throttle.report import FLOOR_LEVER
from blizzard.runner.usage.repository import ContextSampleState

#: This module's public API — the loop steps it owns, in tick order.
__all__ = [
    "Advance",
    "CompleteHeldBindings",
    "ContextSample",
    "Fill",
    "Pull",
    "Reap",
    "Resume",
    "Retention",
    "SpendCeiling",
    "Step",
]

_log = get_logger("blizzard.runner.loop")

# The env count a chunk gets when nothing says otherwise.
_DEFAULT_ENV_COUNT = 1

# Crash points (``bzh:crash-point-registry``): armed, each SIGKILLs the tick subprocess at
# the boundary it guards; unarmed, a module-global string compare and a no-op.

# PULL — the single outbound flusher (store-and-forward drain).
_CP_PULL_BEFORE = crashpoint(
    "pull.before-flush", "entered PULL; registry synced, leases and escalations reconciled, buffer not drained"
)
_CP_PULL_AFTER = crashpoint("pull.after-flush", "PULL done; buffer drained as far as it could")

# The crossing rides `event.recorded`, not a fact kind of its own — both hubs already ingest that
# lane. `(severity, kind)` per `src/blizzard/runner/lifecycle/attempt.py`; the kind is the EVENT's, never a fact's.
_CONTEXT_WARNED: EventLogKind = "worker-context-warned"


@dataclass(frozen=True)
class Step:
    """One reconciliation step over a tick's context — individually runnable, so a test or
    a CLI verb can drive exactly one (``bzh:steppable-loop``)."""

    ctx: LoopContext

    def run(self) -> None:
        raise NotImplementedError


class SpendCeiling(Step):
    """The tick-level kill-switch — first in the tick."""

    def run(self) -> None:
        """Engage the local pause brake once spend reaches the ceiling; runs **first** in the tick so a
        crossing is visible to every later step in the same pass."""
        SpendCeilingCheck(self.ctx).run()


class Reap(Step):
    def run(self) -> None:
        """Expire leases whose worker is gone or **stalled** — each a failed attempt.

        An **orphan** (minted but never spawned) and a **stalled-but-alive** worker end here.
        An exited session-bearing worker is ADVANCE's: exit is the done declaration, and the
        conservative staleness threshold is what keeps the two apart."""
        ReapSweep(self.ctx).run()


class Resume(Step):
    def run(self) -> None:
        """Re-attach to in-flight sessions a restart marked for same-lease resume — startup recovery."""
        RestartResume(self.ctx).run()


class Pull(Step):
    def run(self) -> None:
        """Exchange facts with the hub: sync the registry, reconcile ownership, drain the buffer.

        Reconciliation runs BEFORE the drain, so a preempted lease's queued submission still
        reaches the hub and the drain absorbs the stale-epoch rejection against a lease already
        closed — the retry budget the move must not spend is never reached."""
        self._sync_registry()
        OwnershipReconcile(self.ctx).run()
        _CP_PULL_BEFORE.reached()
        OutboundDrain(self.ctx).run()
        _CP_PULL_AFTER.reached()

    def _sync_registry(self) -> None:
        """``src/blizzard/runner/lifecycle/registration.py``'s ``Registration``."""
        Registration(self.ctx).run()


class CompleteHeldBindings(Step):
    def run(self) -> None:
        HeldBindingCompletion(self.ctx).complete()


class Fill(Step):
    def run(self) -> None:
        """Keep the fleet busy: peek → acquire → claim-by-route → bind → spawn.

        Each binding's open slots are its ``agents`` less the open leases counted against it; for each,
        peek the binding's project, acquire the chunk's environments all-or-nothing from the binding's
        workspace, and claim the route. Either brake, or the throttle gate declining, stops *new* claims.
        """
        ctx = self.ctx
        brakes = Spawner(ctx).brakes()
        claims = ThrottleGate(ctx).claims(brakes)
        InterruptedClaims(ctx).reconcile(braked=claims.held_back)
        if claims.braked:
            _log.info(
                "paused — no new claims this tick",
                runner_name=ctx.config.runner_name,
                hub_paused=brakes.hub,
                local_paused=brakes.local,
            )
            return
        declining = claims.declining
        if declining is not None:
            hold = declining.floor.hold
            _log.info(
                "declining — no new claims this tick",
                runner_name=ctx.config.runner_name,
                declining=[
                    answer.lever.value if isinstance(answer, LeverAnswer) else FLOOR_LEVER
                    for answer in declining.declining
                ],
                subscription=declining.floor.slug,
                hold=hold.kind if hold is not None else None,
                expected_reset=iso_utc(hold.expected_reset) if hold is not None else None,
            )
            return
        BindingFill(ctx).fill()


class Advance(Step):
    def run(self) -> None:
        AdvanceSweep(self.ctx).run()


class Retention(Step):
    """Prune every append-only observation/report lane once per floor, so none grows without
    bound — each lane's own retention contract lives at its own method (the
    store's `IWriteOutboundRepository.prune_outbound` and its siblings, or the filesystem
    sweep of `WorkerStdoutFiles.sweep`), each its own isolated prune. Gated by `RetentionPasses.due` —
    `bzh:probe-gated-pass`'s floor-only form."""

    def run(self) -> None:
        """One prune per lane, each isolated (each lane its own
        isolated item): one lane's failure must not skip the others, and this step gates nothing
        else in the tick either way."""
        ctx = self.ctx
        now = ctx.clock.now()
        passes = ctx.retention_passes
        if passes is not None and not passes.due(now):
            return
        lanes: tuple[tuple[str, Callable[[], int]], ...] = (
            ("outbound buffer", lambda: ctx.stores.outbound.prune_outbound(now=now)),
            ("heartbeat", lambda: ctx.stores.liveness.prune_heartbeats(now=now)),
            ("external usage sample", lambda: ctx.host_stores.external_usage.prune_external_usage_samples(now=now)),
            ("credential renewal", lambda: ctx.host_stores.external_usage.prune_credential_renewals(now=now)),
            ("subscription usage limit", lambda: ctx.host_stores.usage_limits.prune_usage_limits(now=now)),
            ("runner usage limit", lambda: ctx.stores.usage_limits.prune_usage_limits(now=now)),
            (
                "worker stdout",
                lambda: ctx.worker_files.sweep(
                    now=now, retention=timedelta(days=ctx.config.worker_stdout_retention_days)
                ),
            ),
        )
        for label, prune in lanes:
            try:
                prune()
            except Exception as exc:  # one lane's prune failure must not skip the others
                _log.warning("retention prune failed", lane=label, detail=str(exc))
        # Every lane was attempted, whatever its outcome: a lane that raised retries at the next floor.
        if passes is not None:
            passes.record(now)


class ContextSample(Step):
    """Every running lease's live session context, sampled and warned on — never enforced.

    A graph's `rotate` bounds are evaluated at SPAWN time, leaving a long invocation's inside
    unobserved — where a runaway session spends. This closes that gap, not the enforcement one."""

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
            source = ctx.transcript_source_for(session)
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
