"""SQLAlchemy adapter for the chunk artifacts seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import or_, select

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import ARTIFACT_PREFIX, Id
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.artifacts import ArtifactRow
from blizzard.hub.domain.chunks.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.delivery_read import DeliverySources
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import MARKER_PREFIX, chunk_is_terminal, enqueue_close_intents, latest_epoch


class ChunkArtifactsStore:
    """The chunk's node/step artifacts, including the generic hub command node's own."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def delivery_sources_for(self, chunk_ids: list[str]) -> dict[str, DeliverySources]:
        """Only the requested chunks and delivery families; no fleet artifact scan."""
        markers: dict[str, list[ArtifactRow]] = defaultdict(list)
        closed: dict[str, set[tuple[str, int]]] = defaultdict(set)
        landed: dict[str, dict[str, str]] = defaultdict(dict)
        with self._store.read("delivery_sources_for") as conn:
            for batch in id_batches(chunk_ids):
                for a in conn.execute(
                    select(s.artifacts).where(
                        s.artifacts.c.chunk_id.in_(batch),
                        s.artifacts.c.kind == ArtifactKind.ASSET.value,
                        or_(
                            s.artifacts.c.name.like("merged/%"),
                            s.artifacts.c.name.like("delivery-pr/%"),
                            s.artifacts.c.name == "awaiting-external-merge",
                        ),
                    )
                ):
                    markers[a.chunk_id].append(
                        ArtifactRow(
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
                for row in conn.execute(select(s.delivery_pr_closed).where(s.delivery_pr_closed.c.chunk_id.in_(batch))):
                    closed[row.chunk_id].add((row.repo, row.pr_number))
                for row in conn.execute(
                    select(s.delivery_repo_landed).where(s.delivery_repo_landed.c.chunk_id.in_(batch))
                ):
                    landed[row.chunk_id][row.repo] = row.commit_hash
        return {
            chunk_id: DeliverySources(markers[chunk_id], frozenset(closed[chunk_id]), landed[chunk_id])
            for chunk_id in chunk_ids
        }

    def load_artifacts(self, chunk_id: str) -> list[ArtifactRow]:
        with self._store.read("load_artifacts") as conn:
            return [
                ArtifactRow(
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
                for a in conn.execute(select(s.artifacts).where(s.artifacts.c.chunk_id == chunk_id)).all()
            ]

    def latest_artifact(self, chunk_id: str, name: str) -> ArtifactRow | None:
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
            return ArtifactRow(
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
        self, chunk_id: str, *, node_id: str, node_name: str, epoch: int, name: str, content: str, at: datetime
    ) -> bool:
        """Append one hub-node progress artifact **outside** a transition (#65),
        idempotent per ``(chunk, node, name, epoch)`` — the ``produces:`` re-run skip's
        durable side, and the mid-run marker callback's write. Three guards, all
        returning False: the row's existence absorbs a replay, a terminal chunk fact
        (``chunk_stopped``/``chunk_completed``) absorbs a still-running ``run:`` list whose chunk was stopped
        out from under it — regardless of epoch, since stopping mints none — and the
        chunk's CURRENT epoch absorbs a restart that re-aimed it while the ``run:`` list —
        and the mid-run marker callback it can still invoke — kept going
        (``bzh:epoch-fencing``)."""
        with self._store.write("record_hub_artifact") as conn:
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
            if chunk_is_terminal(conn, chunk_id):
                return False
            if latest_epoch(conn, chunk_id) > epoch:
                return False
            conn.execute(
                s.artifacts.insert().values(
                    artifact_id=Id.mint(ARTIFACT_PREFIX, self._clock).value,
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
                )
            )
            if name.startswith(MARKER_PREFIX):
                enqueue_close_intents(conn, chunk_id, at=at)
            return True


def _conforms_artifacts(x: ChunkArtifactsStore) -> IWriteChunkArtifactsRepository:
    return x
