"""Announcing flow facts: one ``flow-changed`` frame per fact a committed write reports."""

from __future__ import annotations

from collections.abc import Iterable

from blizzard.hub.domain.chunk.flow_writes import FlowFactWritten
from blizzard.hub.domain.delivery.ports.flows import IFlowChangePublisher


def publish_flow_facts(publisher: IFlowChangePublisher, written: Iterable[FlowFactWritten]) -> None:
    """Publish one ``flow-changed`` frame per fact in ``written``, in write order. Called only once the write that
    appended them has committed, never under its lock."""
    for fact in written:
        publisher.publish_flow_changed(
            fact.flow_id,
            kind=fact.kind,
            submission_id=fact.submission_id,
            chunk_id=fact.chunk_id,
            outcome=fact.outcome,
        )
