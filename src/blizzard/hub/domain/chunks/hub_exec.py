"""The chunk-hub-exec repository seam (blizzard#411) — the generic hub command node's
(#65) fleet-wide serialization slot and its transition/poll bookkeeping."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from blizzard.hub.domain.artifacts import ArtifactRow


class IReadChunkHubExecRepository(Protocol):
    """Read-only chunk-hub-exec access."""

    def count_live_hub_exec_slots(self) -> int:
        """The number of currently-live hub-execution slots — the invariant checker's
        ``hub:one-live-exec-slot`` probe (should never exceed 1)."""
        ...


class IWriteChunkHubExecRepository(IReadChunkHubExecRepository, Protocol):
    """Read-write chunk-hub-exec access."""

    def acquire_hub_exec_slot(self, chunk_id: str, *, node_id: str, at: datetime, stale_after: timedelta) -> str | None:
        """Acquire the fleet-wide hub-execution serialization slot, or ``None`` if busy.
        A FACT-based lease (``bzh:facts-not-status``), not an in-process lock: insert-if-
        none-live in one transaction. Reentrant for the chunk that already holds it; a slot
        held by another defers unless older than ``stale_after``, when it is reclaimed."""
        ...

    def release_hub_exec_slot(self, chunk_id: str, *, at: datetime) -> None:
        """Release ``chunk_id``'s live hub-execution slot, if any — idempotent."""
        ...

    def record_hub_step_transition(
        self,
        chunk_id: str,
        *,
        from_node_id: str,
        to_node_id: str,
        choice_name: str,
        epoch: int,
        runner_id: str,
        transition_id: str,
        at: datetime,
        artifacts: list[ArtifactRow],
        release_route: bool,
    ) -> bool:
        """Record a generic hub command node's exit transition, atomically and idempotently
        (#65). The hub lease and the transition land in one transaction; ``release_route``
        is True only when ``to_node_id`` is the reserved terminal. Two guards, False either
        way: the transition's existence at ``(chunk_id, from_node_id, epoch)`` absorbs a
        redelivery replay, and the chunk's current epoch absorbs a restart landed mid-``run:``."""
        ...

    def record_hub_node_poll(self, chunk_id: str, *, node_id: str, epoch: int, at: datetime) -> None:
        """Append one pending-poll-attempt fact (#66) — never a transition.

        Append-only: an at-least-once poll attempt is harmless to record twice — it only
        widens the interval/timeout gating's read — so this carries no idempotency guard."""
        ...
