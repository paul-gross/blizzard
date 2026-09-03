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

    def already_delivered(self, *, chunk_id: str, node_id: str, epoch: int) -> bool:
        with self._store.read("already_delivered") as conn:
            return self._marker(conn, chunk_id=chunk_id, node_id=node_id, epoch=epoch) is not None

    @staticmethod
    def _marker(conn, *, chunk_id: str, node_id: str, epoch: int):  # type: ignore[no-untyped-def]
        return conn.execute(
            select(artifacts.c.artifact_id).where(
                (artifacts.c.chunk_id == chunk_id)
                & (artifacts.c.node_id == node_id)
                & (artifacts.c.epoch == epoch)
                & (artifacts.c.name == _DELIVERED_MARKER_NAME)
            )
        ).first()

    def deliver(self, plan: DeliveryPlan) -> DeliveryOutcome:
        with self._store.write("deliver") as conn:
            # Not `ChunkArtifactsStore.record_hub_artifact`: that opens its own transaction, which
            # cannot fold into this one alongside every insert below.
            already = self._marker(conn, chunk_id=plan.chunk_id, node_id=plan.node_id, epoch=plan.epoch)
            if already is not None:
                return DeliveryOutcome.ALREADY_RECORDED

            # Broader than the marker above: a fresh (node, epoch) can still resolve an
            # already-materialized artifact, which would trip the unique constraint raw.
            surviving_deltas = plan.deltas
            ref_substitutions: dict[str, str] = {}
            uninserted_finding_ids: set[str] = set()
            if plan.deltas:
                already_materialized = {
                    row.artifact_id: row.finding_set_id
                    for row in conn.execute(
                        select(finding_sets.c.artifact_id, finding_sets.c.finding_set_id).where(
                            finding_sets.c.artifact_id.in_([d.finding_set.artifact_id for d in plan.deltas])
                        )
                    )
                }
                surviving_deltas = [d for d in plan.deltas if d.finding_set.artifact_id not in already_materialized]
                uninserted_finding_ids = {
                    f.finding_id
                    for d in plan.deltas
                    if d.finding_set.artifact_id in already_materialized
                    for f in d.new_findings
                }
                # A dropped delta's own `add` op still minted a fresh id this visit that is
                # never inserted; substitute the id the earlier visit recorded for that ref.
                dropped_refs = {
                    (already_materialized[d.finding_set.artifact_id], fact.ref): fact.finding_id
                    for d in plan.deltas
                    if d.finding_set.artifact_id in already_materialized
                    for fact in d.facts
                    if fact.kind == "add" and fact.ref is not None
                }
                if dropped_refs:
                    already_recorded_by_ref = {
                        (row.finding_set_id, row.ref): row.finding_id
                        for row in conn.execute(
                            select(
                                finding_facts.c.finding_set_id, finding_facts.c.ref, finding_facts.c.finding_id
                            ).where(
                                finding_facts.c.finding_set_id.in_({fsid for fsid, _ in dropped_refs})
                                & (finding_facts.c.kind == "add")
                                & finding_facts.c.ref.isnot(None)
                            )
                        )
                    }
                    # A set materialized before `finding_facts.ref` existed recorded no ref
                    # to resolve against; that citation stays unresolved rather than raising.
                    ref_substitutions = {
                        finding_id: already_recorded_by_ref[key]
                        for key, finding_id in dropped_refs.items()
                        if key in already_recorded_by_ref
                    }

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
            if facts:
                conn.execute(
                    insert(finding_facts),
                    [
                        {
                            "finding_id": fact.finding_id,
                            "kind": fact.kind,
                            "recorded_at": plan.at,
                            "note": fact.note,
                            "finding_set_id": fact.finding_set_id,
                            "ref": fact.ref,
                        }
                        for fact in facts
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
                # An unresolved citation names a finding this visit never inserted, so its
                # link is dropped like the delta was rather than pointing at nothing.
                links = [
                    {"proposal_id": p.proposal_id, "finding_id": ref_substitutions.get(finding_id, finding_id)}
                    for p in surviving_proposals
                    for finding_id in p.finding_ids
                    if finding_id in ref_substitutions or finding_id not in uninserted_finding_ids
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
