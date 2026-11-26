"""Seed a hub node visit's artifacts and landings through the artifacts repository, the path a hub action's effects
record by."""

from __future__ import annotations

from datetime import datetime

from blizzard.hub.domain.chunk.model import CloseEnqueueDecision, decide_close_enqueue
from blizzard.hub.domain.chunk.no_code import no_code_marker
from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunk.terminal import terminal_under_lock
from blizzard.hub.domain.config.repositories import IReadRepositoryRecordRepository
from blizzard.hub.domain.kernel.transactions import LockedTransaction
from blizzard.hub.work_sources.source import IWorkSourceRegistry
from tests.support.scaffolding import HubHarness, chunk_stores, chunk_unit, hub_store_connections


def _close_decision(hub: HubHarness, chunk_id: str) -> CloseEnqueueDecision:
    """The close intents a landing on the chunk owes, judged against the hub's own work sources."""
    chunk = hub.default_adapter(IReadChunkRecordRepository).get(chunk_id)
    assert chunk is not None, chunk_id
    return decide_close_enqueue(
        chunk.work_refs, lambda source: hub.default_adapter(IWorkSourceRegistry).closer(source) is not None
    )


def record_hub_asset(
    hub: HubHarness, chunk_id: str, *, node_id: str, node_name: str, epoch: int, name: str, content: str
) -> bool:
    """Record ``name`` on the visit ``(chunk_id, node_id, epoch)``, idempotently per that visit and name, handed the
    terminal verdict a hub caller reads under the unit's lock; answers whether it wrote."""
    close_decision = _close_decision(hub, chunk_id)
    stores = chunk_stores(hub.engine, hub.clock)
    with chunk_unit(hub_store_connections(hub.engine), chunk_id) as unit:
        return stores.artifacts.record_hub_artifact(
            chunk_id,
            node_id=node_id,
            node_name=node_name,
            epoch=epoch,
            admission=EpochAdmission.AT_OR_ABOVE,
            terminal=terminal_under_lock(stores.facts, chunk_id, transaction=unit),
            name=name,
            content=content,
            at=hub.clock.now(),
            close_decision=close_decision,
            transaction=unit,
        ).wrote


def record_no_code(
    hub: HubHarness, chunk_id: str, *, node_id: str, node_name: str, repo: str, branch: str, commit: str
) -> None:
    """Record on the visit at epoch 1 the ``no-code/`` marker bound to ``repo``'s declaration of ``branch`` at
    ``commit``, resolved to the hub's one repository record."""
    (record,) = hub.default_adapter(IReadRepositoryRecordRepository).list_all(include_retired=False)
    name, content = no_code_marker(
        {
            "repo": repo,
            "record": str(record.repository_id),
            "base_branch": record.fields.base_branch,
            "branch": branch,
            "commit": commit,
        }
    )
    assert record_hub_asset(hub, chunk_id, node_id=node_id, node_name=node_name, epoch=1, name=name, content=content)


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
        terminal=False,
        repo=repo,
        revision=commit_hash,
        repository_id=repository_id,
        at=at,
        close_decision=close_decision,
        transaction=transaction,
    )
