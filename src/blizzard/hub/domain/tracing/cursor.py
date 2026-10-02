"""The hub's trace export cursor key (``blizzard-product:/plans/tracing/fleet-spans/spec/emission.md`` §The cursor)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.hub.domain.tracing.steps import NodeStep


@dataclass(frozen=True, order=True)
class CursorKey:
    """A position in the total order of closed steps: closing time, chunk, epoch, decision.

    ``decision_id`` is empty for runner and hub steps. :meth:`opening` is the position just before
    every step that closes at an instant, which is where a start or a jump puts the cursor."""

    at: datetime
    chunk_id: str = ""
    epoch: int = 0
    decision_id: str = ""

    @classmethod
    def of(cls, step: NodeStep) -> CursorKey:
        if step.close is None:
            raise ValueError(f"step {step.key.text()} is open and has no cursor position")
        return cls(step.close.at, step.key.chunk_id, step.epoch, step.decision_id or "")

    @classmethod
    def opening(cls, at: datetime) -> CursorKey:
        return cls(at)
