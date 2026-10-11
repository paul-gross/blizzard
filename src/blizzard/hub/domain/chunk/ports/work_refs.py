"""The chunk-work-refs repository seam — the wrapped work items a chunk
holds, and merge-group survivorship over them."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.model import WorkRef, holds_work_refs
from blizzard.hub.domain.chunk.ports.exclusive import ILockedChunkRead
from blizzard.hub.tenancy import TenantId


def resolve_live_holder(chunk_ids: Iterable[str], statuses: Mapping[str, ChunkStatus]) -> str | None:
    """The live-holder tie-break every liveness read shares (``bzh:domain-takes-objects``):
    the lowest id among ``chunk_ids`` still holding its work refs (:func:`holds_work_refs`) — more than one live
    holder shouldn't happen, so the lowest id picks deterministically. ``chunk_ids`` is
    assumed already stripped of ephemeral holders; a chunk id absent from ``statuses``
    reads as :class:`ChunkStatus.READY` (unrecorded facts)."""
    live = sorted(c for c in chunk_ids if holds_work_refs(statuses.get(c, ChunkStatus.READY)))
    return live[0] if live else None


def resolve_live_holders(
    pointer_chunk_ids: Iterable[tuple[WorkRef, str]], statuses: Mapping[str, ChunkStatus]
) -> dict[WorkRef, str]:
    """`resolve_live_holder`'s batched sibling: groups ``pointer_chunk_ids`` by pointer,
    then resolves each group's holder; a pointer with no live holder has no entry at all,
    never mapped to None."""
    candidates: dict[WorkRef, list[str]] = defaultdict(list)
    for pointer, chunk_id in pointer_chunk_ids:
        candidates[pointer].append(chunk_id)
    result: dict[WorkRef, str] = {}
    for pointer, chunk_ids in candidates.items():
        holder = resolve_live_holder(chunk_ids, statuses)
        if holder is not None:
            result[pointer] = holder
    return result


@domain_model
@dataclass(frozen=True)
class WorkRefsSignature:
    """A cheap aggregate fingerprint of every input :meth:`IReadChunkWorkRefsRepository.live_work_refs`
    reads: per table, its row count and, where it has an integer ``id``, its highest. Every table
    is append-only, so two equal signatures mean no live work ref or status moved. No per-row
    content is read."""

    marks: tuple[tuple[str, int, int | None], ...]


@domain_model
@dataclass(frozen=True)
class TenantWorkRef:
    """One work-ref row of the hub-wide corpus: the pointer, the chunk holding it, and the tenant
    both belong to."""

    tenant: TenantId
    chunk_id: str
    ref: WorkRef


def live_work_ref_statuses(
    rows: Iterable[TenantWorkRef], statuses: Mapping[TenantId, Mapping[str, ChunkStatus]]
) -> dict[WorkRef, ChunkStatus]:
    """Every pointer in ``rows`` whose chunk still holds its work refs, with that chunk's status,
    each chunk's status read from its own tenant's ``statuses``; a row whose tenant or chunk carries
    no status — the chunk minted after the statuses were read, or terminal — is excluded."""
    result: dict[WorkRef, ChunkStatus] = {}
    for row in rows:
        status = statuses.get(row.tenant, {}).get(row.chunk_id)
        if status is not None and holds_work_refs(status):
            result[row.ref] = status
    return result


class IReadWorkRefsCorpus(Protocol):
    """The forge-status sweep's corpus, read across every open tenant."""

    def live_work_refs(self) -> Sequence[TenantWorkRef]:
        """Every open tenant's work-ref rows whose chunk is neither grouped away nor deleted."""
        ...

    def live_work_refs_signature(self) -> WorkRefsSignature:
        """A one-statement, hub-wide fingerprint of what :meth:`live_work_refs` and the status
        derivation read — the change probe that lets the sweep skip the unbounded read."""
        ...


class IReadChunkWorkRefsRepository(Protocol):
    """Read-only chunk-work-refs access."""

    def find_live_holder(self, pointer: WorkRef) -> str | None:
        """The chunk_id of a live (non-terminal) chunk holding ``pointer``, or None."""
        ...

    def live_holders(self, pointers: Iterable[WorkRef]) -> dict[WorkRef, str]:
        """`find_live_holder`'s batched sibling — every pointer with a live (non-terminal)
        chunk holding it, keyed by pointer; a pointer with no live holder has no entry
        at all, never mapped to None."""
        ...

    def live_work_refs(self) -> dict[WorkRef, ChunkStatus]:
        """Every work ref held by one of the tenant's live (non-terminal) chunks, with that
        chunk's derived status — the inverse of :meth:`find_live_holder`."""
        ...

    def live_work_refs_signature(self) -> WorkRefsSignature:
        """A one-statement fingerprint of what :meth:`live_work_refs` reads — the change probe
        that lets a standing sweep skip the unbounded read while it is unchanged."""
        ...


class IWriteChunkWorkRefsRepository(IReadChunkWorkRefsRepository, Protocol):
    """Read-write chunk-work-refs access."""

    def add_work_refs_locked(
        self, handle: ILockedChunkRead, chunk_id: str, pointers: list[WorkRef], *, at: datetime
    ) -> None:
        """Fold work refs into a group survivor, de-duped by (source, ref), on ``handle``'s
        already-locked connection (``bzh:store-exclusive-write``)."""
        ...
