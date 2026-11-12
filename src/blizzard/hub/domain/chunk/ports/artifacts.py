"""The chunk-artifacts repository seam — a chunk's produced artifact rows,
including the hub-node marker/log artifacts written outside a transition."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.delivery_read import DeliverySources
from blizzard.hub.domain.chunk.model import CloseEnqueueDecision
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.kernel.transactions import LockedTransaction, Transaction


@domain_model
@dataclass(frozen=True)
class HubArtifactWrite:
    """What appending a hub-node artifact did: whether it wrote, and the ids of the ``work-item-close-skipped`` event
    rows a landing marker owed."""

    wrote: bool
    close_skipped: tuple[int, ...] = ()


@domain_model
@dataclass(frozen=True)
class ArtifactLandingWrite:
    """What recording a merge did: whether the fence would have refused it, and the ids of the
    ``work-item-close-skipped`` event rows it wrote — written even when refused."""

    refused: bool
    close_skipped: tuple[int, ...] = ()


class IReadChunkArtifactsRepository(Protocol):
    """Read-only chunk-artifacts access."""

    def load_artifacts(self, chunk_id: str, *, transaction: Transaction | None = None) -> list[StoredArtifact]:
        """Every artifact row of a chunk; the caller resolves latest-by-epoch. Read in ``transaction`` when given
        (``bzh:transaction-participation``)."""
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
        terminal: bool,
        name: str,
        content: str,
        at: datetime,
        close_decision: CloseEnqueueDecision,
        transaction: LockedTransaction,
    ) -> HubArtifactWrite:
        """Append one hub-node progress artifact OUTSIDE a transition, idempotent per ``(chunk, node, name, epoch)``
        and behind the write fence (``bzh:epoch-fencing``): a replay or a refusal writes nothing. Written in
        ``transaction``, a unit that locks the chunk (``bzh:transaction-participation``). Answers ``wrote`` True iff
        it wrote."""
        ...

    def record_landing(
        self,
        chunk_id: str,
        *,
        node_id: str,
        node_name: str,
        epoch: int,
        admission: EpochAdmission,
        terminal: bool,
        repo: str,
        revision: str,
        repository_id: str,
        at: datetime,
        close_decision: CloseEnqueueDecision,
        transaction: LockedTransaction,
    ) -> ArtifactLandingWrite:
        """Record a merge that has happened in ``transaction``, a unit that locks the chunk: the ``merged/<repo>``
        marker at ``revision``, its ``delivery_repo_landed`` row stamped with ``repository_id``, and close intents.
        Admitted past the write fence (``bzh:epoch-fencing``) — it records what the forge already did. Idempotent
        per ``(chunk, node, name, epoch)``. Answers whether the fence would have refused it."""
        ...
