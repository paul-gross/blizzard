"""SQLAlchemy adapter for the garden-delivery materialization seam (package-private,
blizzard#393 Phase 3). All ``sqlalchemy`` usage is confined here (``bzh:dependency-
inversion``). One ``store.write`` transaction per :meth:`GardenDeliveryStore.deliver`
call — every row a :class:`DeliveryPlan` carries, plus its own idempotence marker, land
together or not at all."""

from __future__ import annotations

import json

from sqlalchemy import insert, select

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.ids import ARTIFACT_PREFIX, Id
from blizzard.hub.domain.garden_delivery_materialize import (
    DeliveryOutcome,
    DeliveryPlan,
    IWriteGardenDeliveryRepository,
)
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.schema import (
    artifacts,
    finding_facts,
    finding_sets,
    findings,
    garden_proposal_findings,
    garden_proposals,
)

_DELIVERED_MARKER_NAME = "garden-delivered"


class GardenDeliveryStore:
    """Read-write garden-delivery-materialization adapter over the hub store engine. No
    clock (`bzh:injected-clock`) — every timestamp arrives already stamped on
    `DeliveryPlan.at`, so this adapter makes no time decisions, only insert decisions."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def deliver(self, plan: DeliveryPlan) -> DeliveryOutcome:
        with self._store.write("deliver") as conn:
            # Not `ChunkStore.record_hub_artifact`: that opens its own transaction, which
            # cannot fold into this one alongside every insert below.
            already = conn.execute(
                select(artifacts.c.artifact_id).where(
                    (artifacts.c.chunk_id == plan.chunk_id)
                    & (artifacts.c.node_id == plan.node_id)
                    & (artifacts.c.epoch == plan.epoch)
                    & (artifacts.c.name == _DELIVERED_MARKER_NAME)
                )
            ).first()
            if already is not None:
                return DeliveryOutcome.ALREADY_RECORDED

            # Broader than the marker above: a fresh (node, epoch) can still resolve an
            # already-materialized artifact, which would trip the unique constraint raw.
            if plan.finding_sets:
                already_materialized = conn.execute(
                    select(finding_sets.c.finding_set_id).where(
                        finding_sets.c.artifact_id.in_([fs.artifact_id for fs in plan.finding_sets])
                    )
                ).first()
                if already_materialized is not None:
                    return DeliveryOutcome.ALREADY_RECORDED

            if plan.new_findings:
                conn.execute(
                    insert(findings),
                    [
                        {
                            "finding_id": f.finding_id,
                            "routine_name": f.routine_name,
                            "scope_slug": f.scope_slug,
                            "class_": f.class_,
                            "locus": f.locus,
                            "summary": f.summary,
                            "introduced": f.introduced,
                        }
                        for f in plan.new_findings
                    ],
                )
            if plan.facts:
                conn.execute(
                    insert(finding_facts),
                    [
                        {"finding_id": fact.finding_id, "kind": fact.kind, "recorded_at": plan.at, "note": fact.note}
                        for fact in plan.facts
                    ],
                )
            if plan.finding_sets:
                conn.execute(
                    insert(finding_sets),
                    [
                        {
                            "finding_set_id": fs.finding_set_id,
                            "artifact_id": fs.artifact_id,
                            "chunk_id": plan.chunk_id,
                            "scope_slug": fs.scope_slug,
                            "revisions": json.dumps(fs.revisions),
                            "measurement": fs.measurement,
                        }
                        for fs in plan.finding_sets
                    ],
                )
            if plan.proposals:
                conn.execute(
                    insert(garden_proposals),
                    [
                        {
                            "proposal_id": p.proposal_id,
                            "routine_name": p.routine_name,
                            "class_": p.class_,
                            "title": p.title,
                            "body": p.body,
                            "created_at": plan.at,
                        }
                        for p in plan.proposals
                    ],
                )
                links = [
                    {"proposal_id": p.proposal_id, "finding_id": finding_id}
                    for p in plan.proposals
                    for finding_id in p.finding_ids
                ]
                if links:
                    conn.execute(insert(garden_proposal_findings), links)

            conn.execute(
                insert(artifacts).values(
                    artifact_id=Id.mint_at(ARTIFACT_PREFIX, plan.at).value,
                    chunk_id=plan.chunk_id,
                    node_id=plan.node_id,
                    node_name=plan.node_name,
                    epoch=plan.epoch,
                    name=_DELIVERED_MARKER_NAME,
                    kind=ArtifactKind.ASSET.value,
                    data=f"{plan.run.routine_name}/{plan.run.scope_slug}",
                    repo=None,
                    forge=None,
                    produced_at=plan.at,
                )
            )
            return DeliveryOutcome.RECORDED


def _conforms_garden_delivery_store(x: GardenDeliveryStore) -> IWriteGardenDeliveryRepository:
    return x
