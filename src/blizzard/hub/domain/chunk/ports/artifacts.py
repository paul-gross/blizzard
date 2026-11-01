"""The chunk-artifacts repository seam — a chunk's produced artifact rows,
including the hub-node marker/log artifacts written outside a transition."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.delivery_read import DeliverySources
from blizzard.hub.domain.chunk.model import CloseEnqueueDecision
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission


class IReadChunkArtifactsRepository(Protocol):
    """Read-only chunk-artifacts access."""

    def load_artifacts(self, chunk_id: str) -> list[StoredArtifact]:
        """Every artifact row of a chunk; the caller resolves latest-by-epoch."""
        ...

    def load_artifacts_for(self, chunk_ids: Sequence[str]) -> dict[str, list[StoredArtifact]]:
        """`load_artifacts`' bulk sibling (`bzh:bulk-reconstitution`) — each chunk's artifact rows
        in the singular's `(seq, artifact_id)` order, batched by caller-supplied ids. A chunk with no
        artifact rows is absent from the result; read it with `.get(chunk_id, [])` for the singular's `[]`."""
        ...

    def delivery_sources_for(self, chunk_ids: list[str]) -> dict[str, DeliverySources]:
        """Narrowed plural: git commits, merged/no-code/PR/external-merge markers and historical
        per-repo landing hashes and PR closures, batched by caller-supplied ids."""
        ...

    def latest_artifact(self, chunk_id: str, name: str) -> StoredArtifact | None:
        """The chunk's newest artifact row named ``name`` — highest epoch, then latest
        ``produced_at``. ``None`` when no artifact of that name exists."""
        ...

    def latest_artifacts(self, chunk_id: str, names: Sequence[str]) -> dict[str, StoredArtifact]:
        """`latest_artifact`'s batched sibling (`bzh:bulk-reconstitution`) — each name's
        newest row, keyed by name, the same row `latest_artifact` picks. A name with no
        artifact is dropped."""
        ...

    def has_hub_artifact(self, chunk_id: str, *, node_id: str, epoch: int, name: str) -> bool:
        """True iff a marker/log artifact named ``name`` is already recorded for this
        exact (chunk, node, epoch)."""
        ...


class IWriteChunkArtifactsRepository(IReadChunkArtifactsRepository, Protocol):
    """Read-write chunk-artifacts access."""

    def record_hub_artifact(
        self,
        chunk_id: str,
        *,
        node_id: str,
        node_name: str,
        epoch: int,
        admission: EpochAdmission,
        name: str,
        content: str,
        at: datetime,
        close_decision: CloseEnqueueDecision,
    ) -> bool:
        """Append one hub-node progress artifact OUTSIDE a transition.

        Idempotent per ``(chunk, node, name, epoch)`` natural key: a re-run that already
        recorded this artifact writes nothing a second time. Also behind the write fence
        (``bzh:epoch-fencing``): a refused write writes nothing either. Ordinary artifact rows, durable exactly like a
        worker-produced one. Returns True iff it wrote."""
        ...

    def record_landing(
        self,
        chunk_id: str,
        *,
        node_id: str,
        node_name: str,
        epoch: int,
        admission: EpochAdmission,
        repo: str,
        revision: str,
        repository_id: str,
        at: datetime,
        close_decision: CloseEnqueueDecision,
    ) -> bool:
        """Record a merge that has happened in one transaction: the ``merged/<repo>`` marker at ``revision``,
        its ``delivery_repo_landed`` row stamped with ``repository_id``, and the chunk's close intents.

        Admitted past the write fence (``bzh:epoch-fencing``) — a terminal chunk or a stale epoch does not
        refuse it, because it records what the forge already did. Idempotent per ``(chunk, node, name,
        epoch)``: a replay writes nothing a second time. Returns whether the fence would have refused it."""
        ...
