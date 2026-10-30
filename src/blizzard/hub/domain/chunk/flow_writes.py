"""What a write reports of the flow facts it wrote, so its caller announces each once the write has committed."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from blizzard.foundation.hub_event_types import FlowChangeKind
from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class FlowFactWritten:
    """One fact a write appended to a flow's record: on ``flow_id``, for ``submission_id`` of ``chunk_id``, its
    ``outcome`` on a ``turn-ended`` or ``withdrawn`` fact."""

    flow_id: str
    kind: FlowChangeKind
    submission_id: str
    chunk_id: str
    outcome: str | None = None

    @classmethod
    def each(
        cls,
        flow_ids: Iterable[str],
        *,
        kind: FlowChangeKind,
        submission_id: str,
        chunk_id: str,
        outcome: str | None = None,
    ) -> tuple[FlowFactWritten, ...]:
        """The one fact per flow a write appends for one submission."""
        return tuple(
            cls(flow_id=flow_id, kind=kind, submission_id=submission_id, chunk_id=chunk_id, outcome=outcome)
            for flow_id in flow_ids
        )


@domain_model
@dataclass(frozen=True)
class LeverWrite:
    """What a lever's write did: the id of the fact it appended, and the ``withdrawn`` facts of the flow
    submissions it closed."""

    fact_id: int
    withdrawn: tuple[FlowFactWritten, ...] = ()
