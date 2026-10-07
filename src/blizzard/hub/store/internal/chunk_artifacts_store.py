"""SQLAlchemy adapter for the chunk artifacts seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import or_, select, tuple_

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.delivery_read import DeliverySources
from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import (
    enqueue_close_intents,
    fence,
    is_landing_marker,
    lock_chunk_row,
    next_artifact_seq,
)


class ChunkArtifactsStore:
    """The chunk's node/step artifacts, including the generic hub command node's own."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def delivery_sources_for(self, chunk_ids: list[str]) -> dict[str, DeliverySources]:
        """Only the requested chunks and delivery families; no fleet artifact scan. Each
        chunk's markers arrive in durable write order (``artifacts.seq``)."""
        markers: dict[str, list[StoredArtifact]] = defaultdict(list)
        landed: dict[str, dict[str, str]] = defaultdict(dict)
        with self._store.read("delivery_sources_for") as conn:
            for batch in id_batches(chunk_ids):
                for a in conn.execute(
                    select(s.artifacts)
                    .where(
                        s.artifacts.c.chunk_id.in_(batch),
                        s.artifacts.c.kind == ArtifactKind.ASSET.value,
                        or_(
                            s.artifacts.c.name.like("merged/%"),
                            s.artifacts.c.name.like("delivery-pr/%"),
                            s.artifacts.c.name == "awaiting-external-merge",
                        ),
                    )
                    # seq is per-chunk and not unique; chunk_id and artifact_id make the
                    # order total without backend-dependent row order (`bzh:sql-portable`).
                    .order_by(s.artifacts.c.chunk_id, s.artifacts.c.seq, s.artifacts.c.artifact_id)
                ):
                    markers[a.chunk_id].append(
                        StoredArtifact(
                            kind=ArtifactKind.ASSET,
                            name=a.name,
                            data=a.data,
                            repo=None,
                            forge=None,
                            artifact_id=a.artifact_id,
                            chunk_id=a.chunk_id,
                            node_id=a.node_id,
                            node_name=a.node_name,
                            epoch=a.epoch,
                        )
                    )
                for row in conn.execute(
                    select(s.delivery_repo_landed)
                    .where(s.delivery_repo_landed.c.chunk_id.in_(batch))
                    .order_by(s.delivery_repo_landed.c.id)
                ):
                    landed[row.chunk_id][row.repo] = row.commit_hash
        return {chunk_id: DeliverySources(markers[chunk_id], landed[chunk_id]) for chunk_id in chunk_ids}

    def load_artifacts(self, chunk_id: str) -> list[StoredArtifact]:
        with self._store.read("load_artifacts") as conn:
            return [
                StoredArtifact(
                    kind=ArtifactKind(a.kind),
                    name=a.name,
                    data=a.data,
                    repo=a.repo,
                    forge=a.forge,
                    artifact_id=a.artifact_id,
                    chunk_id=a.chunk_id,
                    node_id=a.node_id,
                    node_name=a.node_name,
                    epoch=a.epoch,
                )
                for a in conn.execute(
                    select(s.artifacts)
                    .where(s.artifacts.c.chunk_id == chunk_id)
                    .order_by(s.artifacts.c.seq, s.artifacts.c.artifact_id)
                ).all()
            ]

    def latest_artifact(self, chunk_id: str, name: str) -> StoredArtifact | None:
        with self._store.read("latest_artifact") as conn:
            # `artifact_id` is the always-distinct third term (`bzh:sql-portable`): it
            # settles exact (epoch, produced_at) ties off backend-dependent row order.
            a = conn.execute(
                select(s.artifacts)
                .where((s.artifacts.c.chunk_id == chunk_id) & (s.artifacts.c.name == name))
                .order_by(
                    s.artifacts.c.epoch.desc(), s.artifacts.c.produced_at.desc(), s.artifacts.c.artifact_id.desc()
                )
            ).first()
            if a is None:
                return None
            return StoredArtifact(
                kind=ArtifactKind(a.kind),
                name=a.name,
                data=a.data,
                repo=a.repo,
                forge=a.forge,
                artifact_id=a.artifact_id,
                chunk_id=a.chunk_id,
                node_id=a.node_id,
                node_name=a.node_name,
                epoch=a.epoch,
            )

    def latest_artifacts(self, chunk_id: str, names: Sequence[str]) -> dict[str, StoredArtifact]:
        if not names:
            return {}
        result: dict[str, StoredArtifact] = {}
        with self._store.read("latest_artifacts") as conn:
            for batch in id_batches(names):
                # The winner is the row no newer row of the same (chunk, name) beats, in
                # `latest_artifact`'s own order — one statement, so a superseded blob is never read.
                newer = s.artifacts.alias("newer")
                beaten = (
                    select(newer.c.artifact_id)
                    .where(
                        newer.c.chunk_id == s.artifacts.c.chunk_id,
                        newer.c.name == s.artifacts.c.name,
                        tuple_(newer.c.epoch, newer.c.produced_at, newer.c.artifact_id)
                        > tuple_(s.artifacts.c.epoch, s.artifacts.c.produced_at, s.artifacts.c.artifact_id),
                    )
                    .exists()
                )
                for a in conn.execute(
                    select(s.artifacts).where(
                        (s.artifacts.c.chunk_id == chunk_id) & s.artifacts.c.name.in_(batch), ~beaten
                    )
                ).all():
                    result[a.name] = StoredArtifact(
                        kind=ArtifactKind(a.kind),
                        name=a.name,
                        data=a.data,
                        repo=a.repo,
                        forge=a.forge,
                        artifact_id=a.artifact_id,
                        chunk_id=a.chunk_id,
                        node_id=a.node_id,
                        node_name=a.node_name,
                        epoch=a.epoch,
                    )
        return result

    def has_hub_artifact(self, chunk_id: str, *, node_id: str, epoch: int, name: str) -> bool:
        with self._store.read("has_hub_artifact") as conn:
            return (
                conn.execute(
                    select(s.artifacts.c.artifact_id).where(
                        (s.artifacts.c.chunk_id == chunk_id)
                        & (s.artifacts.c.node_id == node_id)
                        & (s.artifacts.c.epoch == epoch)
                        & (s.artifacts.c.name == name)
                    )
                ).first()
                is not None
            )

    def record_hub_artifact(
        self,
        chunk_id: str,
        *,
        node_id: str,
        node_name: str,
        epoch: int,
        admission: EpochAdmission,
        name: str,
        content: str,
        at: datetime,
    ) -> bool:
        """Append one hub-node progress artifact **outside** a transition (#65),
        idempotent per ``(chunk, node, name, epoch)`` — the ``produces:`` re-run skip's
        durable side, and the mid-run marker callback's write. Returns False, writing
        nothing, on a replay — the row's existence — or when the write fence refuses
        (``bzh:epoch-fencing``)."""
        with self._store.write("record_hub_artifact") as conn:
            lock_chunk_row(conn, chunk_id)
            already = conn.execute(
                select(s.artifacts.c.artifact_id).where(
                    (s.artifacts.c.chunk_id == chunk_id)
                    & (s.artifacts.c.node_id == node_id)
                    & (s.artifacts.c.epoch == epoch)
                    & (s.artifacts.c.name == name)
                )
            ).first()
            if already is not None:
                return False
            if fence(conn, chunk_id, epoch=epoch, admission=admission) is not None:
                return False
            conn.execute(
                s.artifacts.insert().values(
                    artifact_id=Id.mint(IdPrefix.ARTIFACT, self._clock).value,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    node_name=node_name,
                    epoch=epoch,
                    name=name,
                    kind=ArtifactKind.ASSET.value,
                    data=content,
                    repo=None,
                    forge=None,
                    produced_at=at,
                    seq=next_artifact_seq(conn, chunk_id),
                )
            )
            if is_landing_marker(name, content):
                enqueue_close_intents(conn, chunk_id, at=at)
            return True


def _conforms_artifacts(x: ChunkArtifactsStore) -> IWriteChunkArtifactsRepository:
    return x
