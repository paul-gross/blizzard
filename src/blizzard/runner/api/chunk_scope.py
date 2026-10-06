"""Chunk-scoped edge resolution for the operator-local, chunk-keyed worker verbs.

The runner holds no chunk entity (``bzh:facts-not-status``), so each resolver mints a typed
scope of the facts one chunk-keyed operation reads, from the request wiring's read
repositories (``bzh:dependency-injection``)."""

from __future__ import annotations

from fastapi import Request

from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.lifecycle.takeover import TakeoverCloseScope, TakeoverOpenScope
from blizzard.runner.operator.requeue import RequeueScope


def resolved_requeue_scope(chunk_id: str, request: Request) -> RequeueScope:
    """The :class:`RequeueScope` for ``chunk_id``."""
    stores = RunnerWiring.of(request).read_stores()
    return RequeueScope(
        chunk_id=chunk_id,
        open_takeover=stores.takeover.open_takeover_for_chunk(chunk_id),
        open_escalation=stores.escalations.open_escalation_for_chunk(chunk_id),
        held_environment_ids=tuple(b.environment_id for b in stores.environments.bindings_for_chunk(chunk_id)),
    )


def resolved_takeover_open_scope(chunk_id: str, request: Request) -> TakeoverOpenScope:
    """The :class:`TakeoverOpenScope` for ``chunk_id``."""
    stores = RunnerWiring.of(request).read_stores()
    active = stores.lease_record.active_lease_for_chunk(chunk_id)
    return TakeoverOpenScope(
        chunk_id=chunk_id,
        open_takeover=stores.takeover.open_takeover_for_chunk(chunk_id),
        bindings=stores.environments.bindings_for_chunk(chunk_id),
        active_lease=active,
        latest_lease_with_session=stores.lease_record.latest_lease_with_session_for_chunk(chunk_id),
        latest_epoch=stores.lease_record.latest_epoch(chunk_id),
        requeue_pending=chunk_id in stores.requeue.pending_requeue_chunk_ids(),
        active_parked=active is not None and active.lease_id in stores.asks.parked_lease_ids(),
        submission_pending=active is not None and active.lease_id in stores.outbound.pending_submission_lease_ids(),
    )


def resolved_takeover_close_scope(chunk_id: str, request: Request) -> TakeoverCloseScope:
    """The :class:`TakeoverCloseScope` for ``chunk_id``."""
    stores = RunnerWiring.of(request).read_stores()
    return TakeoverCloseScope(chunk_id=chunk_id, open_takeover=stores.takeover.open_takeover_for_chunk(chunk_id))
