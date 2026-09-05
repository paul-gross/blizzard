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


def resolved_requeue_scope(chunk_id: str, request: Request) -> RequeueScope:
    """The chunk-keyed facts :meth:`~blizzard.runner.domain.requeue.RequeueService.requeue`
    reads: whether the chunk carries an open takeover, and its open escalation, if any."""
    stores = RunnerWiring.of(request).read_stores()
    return RequeueScope(
        chunk_id=chunk_id,
        open_takeover=stores.takeover.open_takeover_for_chunk(chunk_id),
        open_escalation=stores.escalations.open_escalation_for_chunk(chunk_id),
    )
