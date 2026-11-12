"""Seed a hub node visit's artifacts and landings through the artifacts repository, the path a hub action's effects
record by."""

from __future__ import annotations

from datetime import datetime

from blizzard.hub.domain.chunk.model import CloseEnqueueDecision, decide_close_enqueue
from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.kernel.transactions import LockedTransaction
from tests.support.scaffolding import HubHarness, chunk_stores, chunk_unit, hub_store_connections


def _close_decision(hub: HubHarness, chunk_id: str) -> CloseEnqueueDecision:
    """The close intents a landing on the chunk owes, judged against the hub's own work sources."""
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None, chunk_id
    return decide_close_enqueue(chunk.work_refs, lambda source: hub.services.work_sources.closer(source) is not None)


def record_hub_asset(
    hub: HubHarness, chunk_id: str, *, node_id: str, node_name: str, epoch: int, name: str, content: str
) -> bool:
    """Record ``name`` on the visit ``(chunk_id, node_id, epoch)``, idempotently per that visit and name; answers
    whether it wrote."""
    close_decision = _close_decision(hub, chunk_id)
    with chunk_unit(hub_store_connections(hub.engine), chunk_id) as unit:
        return (
            chunk_stores(hub.engine, hub.clock)
            .artifacts.record_hub_artifact(
                chunk_id,
                node_id=node_id,
                node_name=node_name,
                epoch=epoch,
                admission=EpochAdmission.AT_OR_ABOVE,
                name=name,
                content=content,
                at=hub.clock.now(),
                close_decision=close_decision,
                transaction=unit,
            )
            .wrote
        )


def record_landing(
    artifacts: IWriteChunkArtifactsRepository,
    chunk_id: str,
    *,
    repo: str,
    commit_hash: str,
    at: datetime,
    close_decision: CloseEnqueueDecision,
    repository_id: str = "rp_landed",
    node_id: str = "nd_land",
    node_name: str = "land",
    epoch: int = 1,
    transaction: LockedTransaction,
) -> None:
    """Record one merge of ``repo`` at ``commit_hash`` as a landing pass records it, in ``transaction``, a unit that
    locks the chunk: its ``merged/<repo>`` marker on the visit, its landing row for ``repository_id`` and the chunk's
    close intents."""
    artifacts.record_landing(
        chunk_id,
        node_id=node_id,
        node_name=node_name,
        epoch=epoch,
        admission=EpochAdmission.AT_OR_ABOVE,
        repo=repo,
        revision=commit_hash,
        repository_id=repository_id,
        at=at,
        close_decision=close_decision,
        transaction=transaction,
    )
