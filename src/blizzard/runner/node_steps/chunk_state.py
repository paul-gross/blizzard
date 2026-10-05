"""A chunk's state as the hub reports it to the runner working its node-steps — its status, route,
pause, epochs, spend, and open gate decision. The hub client maps the wire's status view to it
(``bzh:data-roles``)."""

from __future__ import annotations

from dataclasses import dataclass, field

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class ChunkPause:
    """The chunk's newest pause — who set it and when, an ISO-8601 instant. Carried beside the
    status, never a status of its own."""

    by: str
    set_at: str


@domain_model
@dataclass(frozen=True)
class ChunkSpend:
    """The chunk's usage and cost, summed over recorded invocations. For the partial-cost
    readings, see `src/blizzard/hub/domain/chunk/model.py`'s `UsageTotal`."""

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool
    estimated_cost_usd: float | None = None
    billed_partial: bool = False

    @classmethod
    def zero(cls) -> ChunkSpend:
        return cls(
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_create_tokens=0,
            cost_usd=0.0,
            cost_partial=False,
        )


@domain_model
@dataclass(frozen=True)
class ChunkGate:
    """The chunk's newest gate decision — set once a person decides, and ``transitioned`` once its
    resolving transition is recorded."""

    decision_id: str
    node_id: str
    epoch: int
    resolved_choice: str | None = None
    transitioned: bool = False


@domain_model
@dataclass(frozen=True)
class ChunkState:
    """One chunk's status, the runner its route names, its pause, its latest epoch and its
    operator restarts' epochs (oldest first), its spend, and its gate decision."""

    chunk_id: str
    status: ChunkStatus
    route_runner_id: str | None = None
    pause: ChunkPause | None = None
    latest_epoch: int | None = None
    restart_epochs: list[int] = field(default_factory=list)
    cost: ChunkSpend = field(default_factory=ChunkSpend.zero)
    decision: ChunkGate | None = None
