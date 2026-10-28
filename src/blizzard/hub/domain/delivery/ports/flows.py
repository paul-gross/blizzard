"""The flows repository seam — submissions, their closures, and turns, per tenant."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.delivery.flows import FlowFact, FlowResource, Submission, Turn, Withdrawal


class IReadFlows(Protocol):
    """Read-only flows access."""

    def open_submissions_from(self, floor: int) -> list[Submission]:
        """Every open submission whose arrival is at or above ``floor``, in arrival order, each with the flows it
        holds."""
        ...

    def open_turns(self) -> list[Turn]:
        """Every turn of this tenant with no ``ended_at``."""
        ...

    def submission_for_visit(self, chunk_id: str, *, node_id: str, epoch: int) -> Submission | None:
        """The visit's newest-generation submission, open or closed, or ``None`` when it was never submitted."""
        ...

    def facts_of(self, submission_id: str) -> list[FlowFact]:
        """Every fact the submission wrote, on every flow it holds, in write order."""
        ...


class IWriteFlows(IReadFlows, Protocol):
    """Read-write flows access. Every write runs under the tenant row lock, so arrivals and closures serialize."""

    def submit(
        self,
        chunk_id: str,
        *,
        node_id: str,
        epoch: int,
        generation: int,
        resources: Sequence[FlowResource],
        at: datetime,
    ) -> Submission | None:
        """Submit the visit at ``generation`` to the flows of ``resources``, creating any flow not yet made, at the
        tenant's next arrival, with a ``submitted`` fact per flow — one transaction. Returns ``None``, writing nothing,
        when that generation of the visit is already submitted."""
        ...

    def close(self, submission_id: str, *, outcome: Withdrawal, at: datetime) -> bool:
        """Close the submission ``outcome``, with a ``withdrawn`` fact per flow it holds. Returns False, writing
        nothing, when a closure already exists or the submission has an open turn, whose end writes the closure."""
        ...

    def end_turn_interrupted(self, turn_id: str, *, withdrawal: Withdrawal | None, at: datetime) -> bool:
        """End the open turn ``interrupted``, with a ``turn-ended`` fact per flow its submission holds, and close
        that submission ``withdrawal`` when one is given and no closure exists — one transaction. Returns False,
        writing nothing, when the turn has already ended."""
        ...
