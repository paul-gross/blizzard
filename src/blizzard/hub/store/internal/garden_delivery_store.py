"""SQLAlchemy adapter for the garden-delivery materialization seam (package-private).
All ``sqlalchemy`` usage is confined here (``bzh:dependency-
inversion``). Owns the delivery's idempotence marker, its fence, and the idempotence and guard
reads a :class:`DeliveryPlan` is admitted by, all in the caller's locked unit; every row the plan
mints is written by the repository that owns it."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Row
from sqlalchemy.engine import Connection

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.garden.delivery.materialize import (
    DeliveryOutcome,
    DeliveryPlan,
    DeliverySurvivors,
    IReadGardenDeliveryRepository,
    IWriteGardenDeliveryRepository,
)
from blizzard.hub.domain.garden.findings.model import finding_lock
from blizzard.hub.domain.kernel.transactions import LockedTransaction
from blizzard.hub.store.internal.chunk_rows import fence, next_artifact_seq

# Reads the finding adapter's guard and project queries in the caller's unit.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.finding_store import findings_outside_project, moved_findings
from blizzard.hub.store.schema import artifacts, finding_facts, finding_sets, garden_proposals
from blizzard.hub.store.scoped import ScopedConnections, statements_of
from blizzard.hub.store.transactions import connection_for

_DELIVERED_MARKER_NAME = "garden-delivered"


class GardenDeliveryStore:
    """Read-write garden-delivery-materialization adapter over the hub store engine. No
    clock (`bzh:injected-clock`) — every timestamp arrives already stamped on
    `DeliveryPlan.at`, so this adapter makes no time decisions."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def already_delivered(self, *, chunk_id: str, node_id: str, epoch: int) -> bool:
        with self._store.read("already_delivered") as conn:
            return self._marker(conn, chunk_id=chunk_id, node_id=node_id, epoch=epoch) is not None

    @staticmethod
    def _marker(conn: Connection, *, chunk_id: str, node_id: str, epoch: int) -> Row[Any] | None:
        return conn.execute(
            statements_of(conn)
            .select(artifacts, artifacts.c.artifact_id)
            .where(
                (artifacts.c.chunk_id == chunk_id)
                & (artifacts.c.node_id == node_id)
                & (artifacts.c.epoch == epoch)
                & (artifacts.c.name == _DELIVERED_MARKER_NAME)
            )
        ).first()

    def admit(
        self, plan: DeliveryPlan, *, admission: EpochAdmission, terminal: bool, transaction: LockedTransaction
    ) -> DeliveryOutcome | DeliverySurvivors:
        conn = connection_for(self._store, transaction, chunk=plan.chunk_id)
        # `admit`'s guard judges a state no verb can move until this transaction ends.
        for finding_id in sorted(plan.expect):
            connection_for(self._store, transaction, key=finding_lock(finding_id))
        already = self._marker(conn, chunk_id=plan.chunk_id, node_id=plan.node_id, epoch=plan.epoch)
        if already is not None:
            return DeliveryOutcome.ALREADY_RECORDED
        if fence(conn, plan.chunk_id, epoch=plan.epoch, admission=admission, terminal=terminal) is not None:
            return DeliveryOutcome.FENCED

        # Broader than the marker above: a fresh (node, epoch) can still resolve an
        # already-materialized artifact, which would trip the unique constraint raw.
        surviving_deltas = plan.deltas
        ref_substitutions: dict[str, str] = {}
        uninserted_finding_ids: set[str] = set()
        if plan.deltas:
            already_materialized = {
                row.artifact_id: row.finding_set_id
                for row in conn.execute(
                    self._store.select(finding_sets, finding_sets.c.artifact_id, finding_sets.c.finding_set_id).where(
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
                        self._store.select(
                            finding_facts,
                            finding_facts.c.finding_set_id,
                            finding_facts.c.ref,
                            finding_facts.c.finding_id,
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
                    self._store.select(
                        garden_proposals, garden_proposals.c.source_artifact_id, garden_proposals.c.ref
                    ).where(garden_proposals.c.source_artifact_id.in_({p.source_artifact_id for p in plan.proposals}))
                )
            }
            surviving_proposals = [p for p in plan.proposals if (p.source_artifact_id, p.ref) not in already_delivered]

        facts = [fact for d in surviving_deltas for fact in d.facts]
        # Only facts that will actually be inserted: a dropped, already-materialized delta's ops
        # write nothing, so a finding they name moving is no reason to refuse.
        written = {fact.finding_id for fact in facts}
        if moved_findings(conn, {fid: state for fid, state in plan.expect.items() if fid in written}):
            return DeliveryOutcome.GUARD_LOST
        return DeliverySurvivors(
            deltas=surviving_deltas,
            proposals=surviving_proposals,
            ref_substitutions=ref_substitutions,
            uninserted_finding_ids=uninserted_finding_ids,
        )

    def findings_outside_project(
        self, finding_ids: Sequence[str], project_id: str, *, transaction: LockedTransaction
    ) -> list[str]:
        return findings_outside_project(connection_for(self._store, transaction), finding_ids, project_id)

    def record_marker(self, plan: DeliveryPlan, *, transaction: LockedTransaction) -> None:
        conn = connection_for(self._store, transaction, chunk=plan.chunk_id)
        conn.execute(
            self._store.insert(artifacts).values(
                artifact_id=Id.mint_at(IdPrefix.ARTIFACT, plan.at).value,
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
                seq=next_artifact_seq(conn, plan.chunk_id),
            )
        )


_garden_delivery_store_conforms: type[IWriteGardenDeliveryRepository] = GardenDeliveryStore


_garden_delivery_store_read_conforms: type[IReadGardenDeliveryRepository] = GardenDeliveryStore
