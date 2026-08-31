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
            surviving_deltas = plan.deltas
            if plan.deltas:
                already_materialized = {
                    row.artifact_id
                    for row in conn.execute(
                        select(finding_sets.c.artifact_id).where(
                            finding_sets.c.artifact_id.in_([d.finding_set.artifact_id for d in plan.deltas])
                        )
                    )
                }
                surviving_deltas = [d for d in plan.deltas if d.finding_set.artifact_id not in already_materialized]

            # Same idea, keyed `(source_artifact_id, ref)` — one `--proposals` artifact
            # yields many rows, unlike a delta's one-artifact-to-one-set.
            surviving_proposals = plan.proposals
            if plan.proposals:
                already_delivered = {
                    (row.source_artifact_id, row.ref)
                    for row in conn.execute(
                        select(garden_proposals.c.source_artifact_id, garden_proposals.c.ref).where(
                            garden_proposals.c.source_artifact_id.in_({p.source_artifact_id for p in plan.proposals})
                        )
                    )
                }
                surviving_proposals = [
                    p for p in plan.proposals if (p.source_artifact_id, p.ref) not in already_delivered
                ]

            new_findings = [f for d in surviving_deltas for f in d.new_findings]
            facts = [fact for d in surviving_deltas for fact in d.facts]
            finding_set_rows = [d.finding_set for d in surviving_deltas]

            if new_findings:
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
                            "introduced_at": f.introduced_at,
                        }
                        for f in new_findings
                    ],
                )
            if facts:
                conn.execute(
                    insert(finding_facts),
                    [
                        {"finding_id": fact.finding_id, "kind": fact.kind, "recorded_at": plan.at, "note": fact.note}
                        for fact in facts
                    ],
                )
            if finding_set_rows:
                conn.execute(
                    insert(finding_sets),
                    [
                        {
                            "finding_set_id": fs.finding_set_id,
                            "artifact_id": fs.artifact_id,
                            "chunk_id": plan.chunk_id,
                            "scope_slug": fs.scope_slug,
                            "routine_name": plan.run.routine_name,
                            "revisions": json.dumps(fs.revisions),
                            "measurement": fs.measurement,
                        }
                        for fs in finding_set_rows
                    ],
                )
            if surviving_proposals:
                conn.execute(
                    insert(garden_proposals),
                    [
                        {
                            "proposal_id": p.proposal_id,
                            "routine_name": p.routine_name,
                            "class_": p.class_,
                            "title": p.title,
                            "body": p.body,
                            "source_artifact_id": p.source_artifact_id,
                            "ref": p.ref,
                            "created_at": plan.at,
                        }
                        for p in surviving_proposals
                    ],
                )
                links = [
                    {"proposal_id": p.proposal_id, "finding_id": finding_id}
                    for p in surviving_proposals
                    for finding_id in p.finding_ids
                ]
                if links:
                    conn.execute(insert(garden_proposal_findings), links)

            # Always written, even when every delta was already materialized: this visit
            # is a genuinely new (chunk_id, node_id, epoch) and owes its idempotence key.
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
            if surviving_deltas or surviving_proposals:
                return DeliveryOutcome.RECORDED
            return DeliveryOutcome.ALREADY_RECORDED


def _conforms_garden_delivery_store(x: GardenDeliveryStore) -> IWriteGardenDeliveryRepository:
    return x
