"""A run's identity — the routine, scope, and mode a work item's run executes under.
This seam resolves it from a chunk's first work ref, to its ``work_item_runs`` row."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.model import Chunk


@domain_model
@dataclass(frozen=True)
class RunContext:
    routine_name: str
    scope_slug: str
    mode: str


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


class IReadRunContextRepository(Protocol):
    """Read-only run-context access. Controllers at the edges depend on this variant."""

    def for_chunk(self, chunk: Chunk) -> RunContext | None:
        """Resolve ``chunk``'s run context through its first work ref
        (:attr:`~blizzard.hub.domain.chunk.model.Chunk.work_refs`\\ ``[0]``) — a :class:`WorkRef`
        looked up in ``work_items``, whose ``work_item_id`` is then looked up in
        ``work_item_runs``. ``None`` when ``chunk`` names no work ref, or when its work
        item has no ``work_item_runs`` row."""
        ...


class IWriteRunContextRepository(IReadRunContextRepository, Protocol):
    """Read-write run-context access. Only the domain layer depends on this variant."""

    def record(self, work_item_id: str, context: RunContext) -> None:
        """Record ``context`` as ``work_item_id``'s run identity."""
        ...
