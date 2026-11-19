"""The execution-observation repository seam — the harness's sampled evidence of each lease's
own current execution, and the no-progress warning each stall raises once."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.foundation.store.utc import as_utc
from blizzard.runner.harness.observation import ExecutionObservation

__all__ = [
    "ExecutionObservationState",
    "IReadExecutionObservationRepository",
    "IWriteExecutionObservationRepository",
    "WarnedStall",
]


@domain_model
@dataclass(frozen=True)
class WarnedStall:
    """The stall a no-progress warning last fired on: its generation and its progress anchor."""

    generation: int
    anchor_at: datetime


@domain_model
@dataclass(frozen=True)
class ExecutionObservationState:
    """One lease's sampler state, read together: when it was last observed (the cadence anchor),
    the newest progress any observation reported, and the stall it last warned on."""

    last_observed_at: datetime
    latest_progress_at: datetime | None = None
    warned: WarnedStall | None = None

    def progress_with(self, reported: datetime | None) -> datetime | None:
        """The newer of the recorded progress and the progress this observation ``reported``."""
        if self.latest_progress_at is None:
            return reported
        if reported is None:
            return self.latest_progress_at
        return max(as_utc(reported), self.latest_progress_at)


class IReadExecutionObservationRepository(Protocol):
    """Read-only execution-observation queries."""

    def execution_observation_states(self, lease_ids: Sequence[str]) -> dict[str, ExecutionObservationState]:
        """Each lease's :class:`ExecutionObservationState`, in grouped reads newest per lease rather than
        one round trip per lease (``bzh:bulk-reconstitution``, ``bzh:newest-per-key-read``). A lease
        never observed is absent."""
        ...


class IWriteExecutionObservationRepository(IReadExecutionObservationRepository, Protocol):
    """Read-write execution-observation store."""

    def record_execution_observation(
        self,
        *,
        lease_id: str,
        chunk_id: str,
        generation: int,
        observation: ExecutionObservation,
        observed_at: datetime,
        warned_anchor_at: datetime | None = None,
        report_kind: str = "",
        report_payload: str = "",
    ) -> int | None:
        """Append one observation of ``lease_id``'s generation and, when a report is given, buffer it and
        return its seq, atomically — a warning buffered without its row would re-fire, and a row
        without its warning would suppress it. ``warned_anchor_at`` marks the progress anchor the
        report warned on; an empty ``report_kind`` records the observation alone and returns ``None``."""
        ...

    def prune_execution_observations(self, *, now: datetime) -> int:
        """Compact observations older than the store's own retention window, keeping each lease's
        newest row and its newest warning row regardless of age, so the cadence anchor and the
        warning dedupe answer identically before and after. Returns the number of rows pruned."""
        ...
