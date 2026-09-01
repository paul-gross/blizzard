"""The chunk-artifacts repository seam — a chunk's produced artifact rows,
including the hub-node marker/log artifacts written outside a transition."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.artifacts import ArtifactRow


class IReadChunkArtifactsRepository(Protocol):
    """Read-only chunk-artifacts access."""

    def load_artifacts(self, chunk_id: str) -> list[ArtifactRow]:
        """Every artifact row of a chunk; the caller resolves latest-by-epoch."""
        ...

    def latest_artifact(self, chunk_id: str, name: str) -> ArtifactRow | None:
        """The chunk's newest artifact row named ``name`` — highest epoch, then latest
        ``produced_at`` (blizzard#393 Phase 4) — the garden-delivery route's own
        by-name resolution. ``None`` when no artifact of that name exists."""
        ...

    def has_hub_artifact(self, chunk_id: str, *, node_id: str, epoch: int, name: str) -> bool:
        """True iff a marker/log artifact named ``name`` is already recorded for this
        exact (chunk, node, epoch) — the ``produces:`` re-run skip probe (#65)."""
        ...


class IWriteChunkArtifactsRepository(IReadChunkArtifactsRepository, Protocol):
    """Read-write chunk-artifacts access."""

    def record_hub_artifact(
        self, chunk_id: str, *, node_id: str, node_name: str, epoch: int, name: str, content: str, at: datetime
    ) -> bool:
        """Append one hub-node progress artifact OUTSIDE a transition (#65).

        Idempotent per ``(chunk, node, name, epoch)`` natural key: a re-run that already
        recorded this artifact writes nothing a second time. Ordinary artifact rows,
        durable exactly like a worker-produced one. Returns True iff it wrote."""
        ...
