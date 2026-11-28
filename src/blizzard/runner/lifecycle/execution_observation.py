"""EXECUTION OBSERVE — sample every live lease's current execution through its harness, and warn once per stall."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.event_log import EVENT_LOG_SEVERITY, EventLogKind
from blizzard.foundation.fact_kinds import EVENT_RECORDED
from blizzard.foundation.instants import as_utc, iso_utc
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.runner.events.publisher import IRunnerEventPublisher
from blizzard.runner.harness.observation import (
    ExecutionLiveness,
    ExecutionObservation,
    ExecutionObservationRequest,
)
from blizzard.runner.harness.registry import (
    IHarnessExecutionObservationRegistry,
    UnavailableHarnessError,
    UnknownHarnessError,
)
from blizzard.runner.leases.activity import (
    EXECUTION_OBSERVATION_INTERVAL,
    NO_PROGRESS_THRESHOLD,
    progress_anchor,
    warns_no_progress,
)
from blizzard.runner.leases.asks import IReadAskRepository
from blizzard.runner.leases.execution_observation import (
    ExecutionObservationState,
    IWriteExecutionObservationRepository,
)
from blizzard.runner.leases.liveness import IReadLeaseLivenessRepository, LeaseLivenessFacts
from blizzard.runner.leases.model import Lease
from blizzard.runner.leases.record import IReadLeaseRecordRepository
from blizzard.runner.leases.worker_stdout import IWorkerStdoutFiles
from blizzard.runner.lifecycle.model import TakeoverHolds
from blizzard.runner.lifecycle.takeover import IReadTakeoverRepository

_log = get_logger("blizzard.runner.loop")

# The warning rides `event.recorded`, not a fact kind of its own — both hubs already ingest that lane.
_NO_PROGRESS: EventLogKind = "worker-no-progress"


class ExecutionObservationStores(Protocol):
    @property
    def lease_record(self) -> IReadLeaseRecordRepository: ...
    @property
    def liveness(self) -> IReadLeaseLivenessRepository: ...
    @property
    def asks(self) -> IReadAskRepository: ...
    @property
    def takeover(self) -> IReadTakeoverRepository: ...
    @property
    def execution_observations(self) -> IWriteExecutionObservationRepository: ...


class ExecutionObservationContext(Protocol):
    @property
    def stores(self) -> ExecutionObservationStores: ...
    def execution_observation_harnesses(
        self, lease_id: str, generation: int
    ) -> IHarnessExecutionObservationRegistry: ...
    @property
    def worker_files(self) -> IWorkerStdoutFiles: ...
    @property
    def clock(self) -> IClock: ...
    @property
    def events(self) -> IRunnerEventPublisher | None: ...


@domain_model
@dataclass(frozen=True)
class _Live:
    """One observable lease with the facts its sample is judged against."""

    lease: Lease
    facts: LeaseLivenessFacts
    state: ExecutionObservationState | None


@dataclass(frozen=True)
class ExecutionObservationSweep:
    """Records what each live lease's harness can see of its current execution — evidence REAP
    reads as liveness — and raises the no-progress warning. It records only; a sample being
    taken never moves any clock, and it never kills a worker."""

    ctx: ExecutionObservationContext

    def run(self) -> None:
        """Observe each due lease once. Per-lease failures are isolated: one unreadable execution
        must not cost the other leases their samples."""
        ctx = self.ctx
        now = ctx.clock.now()
        try:
            live = self._observable(now)
        except Exception as exc:  # a failed read skips this step only; the tick goes on
            _log.warning("execution observation step failed", detail=str(exc))
            return
        for entry in live:
            try:
                self._observe(entry, now)
            except Exception as exc:  # one lease's observation must not end the sweep
                _log.warning("execution observation failed", lease_id=entry.lease.lease_id, detail=str(exc))

    def _observable(self, now: datetime) -> list[_Live]:
        """The due leases REAP would judge: spawned and identified, neither parked on a question nor
        held by a takeover — a human wait raises no warning. Every fact in one bulk read each."""
        stores = self.ctx.stores
        leases = stores.lease_record.list_active_leases()
        lease_ids = [lease.lease_id for lease in leases]
        facts_by_lease = stores.liveness.liveness_facts(lease_ids)
        states = stores.execution_observations.execution_observation_states(lease_ids)
        parked = stores.asks.parked_lease_ids()
        takeovers = TakeoverHolds.of(stores.takeover.open_takeovers())
        live: list[_Live] = []
        for lease in leases:
            facts = facts_by_lease.get(lease.lease_id)
            if lease.session is None or lease.pid is None or facts is None or facts.latest_spawn is None:
                continue
            if lease.lease_id in parked or takeovers.holds_lease(lease):
                continue
            state = states.get(lease.lease_id)
            if state is not None and now - state.last_observed_at < EXECUTION_OBSERVATION_INTERVAL:
                continue
            live.append(_Live(lease=lease, facts=facts, state=state))
        return live

    def _observe(self, entry: _Live, now: datetime) -> None:
        ctx = self.ctx
        lease, facts, state = entry.lease, entry.facts, entry.state
        session = lease.session
        spawned_at = facts.latest_spawn
        if session is None or spawned_at is None:
            return
        generation = facts.generation
        try:
            seam = ctx.execution_observation_harnesses(lease.lease_id, generation).execution_observation(
                session.harness_id
            )
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            # A read-only side lane: never escalates on its own; its dispatch paths escalate it.
            _log.warning(
                "execution observation skipped — owner unresolvable",
                lease_id=lease.lease_id,
                harness_id=session.harness_id,
                detail=str(exc),
            )
            return
        request = ExecutionObservationRequest(
            session=session,
            generation=generation,
            stdout_path=ctx.worker_files.stdout_path(lease.lease_id, generation),
            now=now,
        )
        try:
            observation = seam.observe_execution(request)
        except Exception as exc:  # a failed query is no evidence, recorded so the cadence still advances
            observation = ExecutionObservation.unknown(f"observation failed: {exc}")
        progress = observation.progress_at if state is None else state.progress_with(observation.progress_at)
        anchor = progress_anchor(spawn=spawned_at, heartbeat=facts.latest_heartbeat, progress=progress)
        warn = warns_no_progress(
            alive=self._evidenced(observation, spawned_at, now),
            generation=generation,
            anchor=anchor,
            warned=state.warned if state is not None else None,
            now=now,
        )
        seq = ctx.stores.execution_observations.record_execution_observation(
            lease_id=lease.lease_id,
            chunk_id=lease.chunk_id,
            generation=generation,
            observation=observation,
            observed_at=now,
            warned_anchor_at=anchor if warn else None,
            report_kind=EVENT_RECORDED if warn else "",
            report_payload=json.dumps(self._event(lease, generation, observation, anchor, now)) if warn else "",
        )
        if seq is not None and ctx.events is not None:
            ctx.events.publish_fact_changed(
                seq=seq, kind=EVENT_RECORDED, chunk_id=lease.chunk_id, lease_id=lease.lease_id
            )

    @staticmethod
    def _evidenced(observation: ExecutionObservation, spawned_at: datetime, now: datetime) -> bool:
        """Whether this observation is positive evidence of the current generation: ``alive``, with
        evidence produced no earlier than the generation's spawn and no later than now."""
        evidence_at = observation.evidence_at
        if observation.liveness is not ExecutionLiveness.ALIVE or evidence_at is None:
            return False
        return as_utc(spawned_at) <= as_utc(evidence_at) <= now

    @staticmethod
    def _event(
        lease: Lease, generation: int, observation: ExecutionObservation, anchor: datetime, now: datetime
    ) -> dict[str, object]:
        """The ``event.recorded`` payload one stall surfaces, in the shape the hub ingests."""
        evidence_at = observation.evidence_at
        evidence_age = int((now - as_utc(evidence_at)).total_seconds()) if evidence_at is not None else None
        progress_age = int((now - as_utc(anchor)).total_seconds())
        progress_known = observation.progress_at is not None
        threshold_minutes = int(NO_PROGRESS_THRESHOLD.total_seconds() // 60)
        return {
            "severity": EVENT_LOG_SEVERITY[_NO_PROGRESS],
            "kind": _NO_PROGRESS,
            "chunk_id": lease.chunk_id,
            "lease_id": lease.lease_id,
            "node_name": lease.node_name,
            "message": (
                f"worker alive ({observation.activity.value}) but no progress for {progress_age // 60} min, "
                f"past the {threshold_minutes} min line; last evidence {evidence_age}s ago"
                + ("" if progress_known else "; harness progress unknown")
            ),
            "detail": {
                "session_id": lease.session_id,
                "generation": generation,
                "activity": observation.activity.value,
                "evidence_age_seconds": evidence_age,
                "progress_age_seconds": progress_age,
                "progress_anchor_at": iso_utc(anchor),
                "harness_progress_known": progress_known,
                "observed_at": iso_utc(now),
            },
        }
