"""The chunk's view of its flow submissions — what a lever reads to name the ones it withdraws."""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.domain.chunk.flow_writes import OpenSubmission
from blizzard.hub.domain.kernel.transactions import Transaction


class IReadChunkFlowSubmissions(Protocol):
    def open_flow_submissions(
        self, chunk_id: str, *, transaction: Transaction | None = None
    ) -> tuple[OpenSubmission, ...]:
        """The chunk's open flow submissions in arrival order, each with whether it has a turn open — read in
        ``transaction`` when one is passed (``bzh:transaction-participation``), so a lever names what it withdraws
        under the lock its write holds."""
        ...
