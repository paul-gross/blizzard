"""Chunk-scoped edge resolution for the operator-local, chunk-keyed worker verbs.

The runner holds no chunk entity (``bzh:facts-not-status`` keeps each per-concept table
independent), so resolving a ``chunk_id`` path parameter means minting a typed scope of
exactly the facts a chunk-keyed rule reads, rather than loading an aggregate that does
not exist. One resolver function per chunk-keyed operation, sibling to
:mod:`~blizzard.runner.api.lease_scope` and reached the same way — through the runner's
request wiring, its read repositories bound at the composition root
(``bzh:dependency-injection``)."""

from __future__ import annotations

from fastapi import Request

from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.domain.requeue import RequeueScope
from blizzard.runner.domain.takeover import TakeoverCloseScope, TakeoverOpenScope


def resolved_requeue_scope(chunk_id: str, request: Request) -> RequeueScope:
    """The chunk-keyed facts :meth:`~blizzard.runner.domain.requeue.RequeueService.requeue`
    reads: whether the chunk carries an open takeover, and its open escalation, if any."""
    stores = RunnerWiring.of(request).read_stores()
    return RequeueScope(
        chunk_id=chunk_id,
        open_takeover=stores.takeover.open_takeover_for_chunk(chunk_id),
        open_escalation=stores.escalations.open_escalation_for_chunk(chunk_id),
    )


def resolved_takeover_open_scope(chunk_id: str, request: Request) -> TakeoverOpenScope:
    """The chunk-keyed facts :meth:`~blizzard.runner.domain.takeover.TakeoverService.open`
    reads: the open takeover, the held bindings, the active and latest leases, and the
    fence-epoch floor."""
    stores = RunnerWiring.of(request).read_stores()
    return TakeoverOpenScope(
        chunk_id=chunk_id,
        open_takeover=stores.takeover.open_takeover_for_chunk(chunk_id),
        bindings=stores.environments.bindings_for_chunk(chunk_id),
        active_lease=stores.lease_record.active_lease_for_chunk(chunk_id),
        latest_lease=stores.lease_record.latest_lease_for_chunk(chunk_id),
        latest_epoch=stores.lease_record.latest_epoch(chunk_id),
    )


def resolved_takeover_close_scope(chunk_id: str, request: Request) -> TakeoverCloseScope:
    """The chunk-keyed fact :meth:`~blizzard.runner.domain.takeover.TakeoverService.close`
    reads: the chunk's open takeover, if any."""
    stores = RunnerWiring.of(request).read_stores()
    return TakeoverCloseScope(chunk_id=chunk_id, open_takeover=stores.takeover.open_takeover_for_chunk(chunk_id))
