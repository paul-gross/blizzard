"""SQLAlchemy adapter for the chunk artifacts seam (package-private, blizzard#411 Phase 3).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import ARTIFACT_PREFIX, Id
from blizzard.hub.domain.artifacts import ArtifactRow
from blizzard.hub.domain.chunks.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import enqueue_close_intents

# The generic ``merged/<repo>`` landing marker (issue #67) — mirrors domain/work.py's own
# copy (``LandedRepos``'s), which reads it back; each side owns its own constant.
_MARKER_PREFIX = "merged/"


class ChunkArtifactsStore:
    """The chunk's node/step artifacts, including the generic hub command node's own."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

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
        durable side, and the mid-run marker callback's write."""
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
            if name.startswith(_MARKER_PREFIX):
                enqueue_close_intents(conn, chunk_id, at=at)
            return True


def _conforms_artifacts(x: ChunkArtifactsStore) -> IWriteChunkArtifactsRepository:
    return x
