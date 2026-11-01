"""SQLAlchemy adapter for the chunk artifacts seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Row, or_

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.delivery_read import DeliverySources, PrMarker
from blizzard.hub.domain.chunk.model import CloseEnqueueDecision
from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import (
    enqueue_close_intents,
    fence,
    is_landing_marker,
    lock_chunk_row,
    next_artifact_seq,
    write_landing_rows,
)
from blizzard.hub.store.internal.newest_fact import newer_row_exists
from blizzard.hub.store.scoped import ScopedConnections


def _pr_marker(name: str, epoch: int, data: str) -> PrMarker | None:
    """The PR a ``delivery-pr/`` marker's JSON payload names, or ``None`` when it is not that shape."""
    try:
        loaded: object = json.loads(data)
    except ValueError:
        return None
    if not isinstance(loaded, dict):
        return None
    payload = cast(dict[str, Any], loaded)
    if not (
        isinstance(payload.get("repo"), str)
        and isinstance(payload.get("number"), int)
        and isinstance(payload.get("url"), str)
    ):
        return None
    return PrMarker(name=name, epoch=epoch, repo=payload["repo"], number=payload["number"], url=payload["url"])


def _stored_artifact(a: Row[Any]) -> StoredArtifact:
    """One full ``artifacts`` row as the domain's :class:`StoredArtifact`."""
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
        produced_at=a.produced_at,
    )


class ChunkArtifactsStore:
    """The chunk's node/step artifacts, including the generic hub command node's own."""

    def __init__(self, store: ScopedConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def delivery_sources_for(self, chunk_ids: list[str]) -> dict[str, DeliverySources]:
        """Only the requested chunks and delivery families; no fleet artifact scan. Each
        chunk's markers arrive in durable write order (``artifacts.seq``)."""
        markers: dict[str, list[StoredArtifact]] = defaultdict(list)
        pr_markers: dict[str, list[PrMarker]] = defaultdict(list)
        landed: dict[str, dict[str, str]] = defaultdict(dict)
        commits: dict[str, list[StoredArtifact]] = defaultdict(list)
        with self._store.read("delivery_sources_for") as conn:
            for batch in id_batches(chunk_ids):
                for a in conn.execute(
                    self._store.select(s.artifacts)
                    .where(
                        s.artifacts.c.chunk_id.in_(batch),
                        or_(
                            s.artifacts.c.kind == ArtifactKind.GIT_COMMIT.value,
                            (s.artifacts.c.kind == ArtifactKind.ASSET.value)
                            & or_(
                                s.artifacts.c.name.like("merged/%"),
                                s.artifacts.c.name.like("no-code/%"),
                                s.artifacts.c.name.like("delivery-pr/%"),
                                s.artifacts.c.name == "awaiting-external-merge",
                            ),
                        ),
                    )
                    # seq is per-chunk and not unique; chunk_id and artifact_id make the
                    # order total without backend-dependent row order (`bzh:sql-portable`).
                    .order_by(s.artifacts.c.chunk_id, s.artifacts.c.seq, s.artifacts.c.artifact_id)
                ):
                    if a.kind == ArtifactKind.GIT_COMMIT.value:
                        commits[a.chunk_id].append(
                            StoredArtifact(
                                ArtifactKind.GIT_COMMIT,
                                a.name,
                                a.data,
                                a.repo,
                                a.forge,
                                a.artifact_id,
                                a.chunk_id,
                                a.node_id,
                                a.node_name,
                                a.epoch,
                            )
                        )
                        continue
                    if a.name.startswith("delivery-pr/"):
                        # A payload that is not a PR reference is skipped here, so the domain only ever
                        # folds well-formed markers.
                        pr = _pr_marker(a.name, a.epoch, a.data)
                        if pr is not None:
                            pr_markers[a.chunk_id].append(pr)
                        continue
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
                # The newest landing per (chunk, repo) — the last of the ascending-id history the marker
                # read above leaves to its consumer's fold — so a re-landed repo costs one row.
                later = s.delivery_repo_landed.alias("later")
                beaten = newer_row_exists(
                    self._store,
                    later,
                    partition=[
                        (s.delivery_repo_landed.c.chunk_id, later.c.chunk_id),
                        (s.delivery_repo_landed.c.repo, later.c.repo),
                    ],
                    order=[(s.delivery_repo_landed.c.id, later.c.id)],
                )
                for row in conn.execute(
                    self._store.select(s.delivery_repo_landed).where(
                        s.delivery_repo_landed.c.chunk_id.in_(batch), ~beaten
                    )
                ):
                    landed[row.chunk_id][row.repo] = row.commit_hash
        return {
            chunk_id: DeliverySources(markers[chunk_id], pr_markers[chunk_id], landed[chunk_id], commits[chunk_id])
            for chunk_id in chunk_ids
        }

    def load_artifacts(self, chunk_id: str) -> list[StoredArtifact]:
        with self._store.read("load_artifacts") as conn:
            return [
                _stored_artifact(a)
                for a in conn.execute(
                    self._store.select(s.artifacts)
                    .where(s.artifacts.c.chunk_id == chunk_id)
                    .order_by(s.artifacts.c.seq, s.artifacts.c.artifact_id)
                ).all()
            ]

    def load_artifacts_for(self, chunk_ids: Sequence[str]) -> dict[str, list[StoredArtifact]]:
        grouped: dict[str, list[StoredArtifact]] = {}
        with self._store.read("load_artifacts_for") as conn:
            for batch in id_batches(chunk_ids):
                for a in conn.execute(
                    self._store.select(s.artifacts)
                    .where(s.artifacts.c.chunk_id.in_(batch))
                    .order_by(s.artifacts.c.chunk_id, s.artifacts.c.seq, s.artifacts.c.artifact_id)
                ).all():
                    grouped.setdefault(a.chunk_id, []).append(_stored_artifact(a))
        return grouped

    def latest_artifact(self, chunk_id: str, name: str) -> StoredArtifact | None:
        with self._store.read("latest_artifact") as conn:
            # `artifact_id` is the always-distinct third term (`bzh:sql-portable`): it
            # settles exact (epoch, produced_at) ties off backend-dependent row order.
            a = conn.execute(
                self._store.select(s.artifacts)
                .where((s.artifacts.c.chunk_id == chunk_id) & (s.artifacts.c.name == name))
                .order_by(
                    s.artifacts.c.epoch.desc(), s.artifacts.c.produced_at.desc(), s.artifacts.c.artifact_id.desc()
                )
                .limit(1)
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
                beaten = newer_row_exists(
                    self._store,
                    newer,
                    partition=[(s.artifacts.c.chunk_id, newer.c.chunk_id), (s.artifacts.c.name, newer.c.name)],
                    order=[
                        (s.artifacts.c.epoch, newer.c.epoch),
                        (s.artifacts.c.produced_at, newer.c.produced_at),
                        (s.artifacts.c.artifact_id, newer.c.artifact_id),
                    ],
                )
                for a in conn.execute(
                    self._store.select(s.artifacts).where(
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
                    self._store.select(s.artifacts, s.artifacts.c.artifact_id).where(
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
        close_decision: CloseEnqueueDecision,
    ) -> bool:
        """See ``src/blizzard/hub/domain/chunk/ports/artifacts.py``'s
        ``IWriteChunkArtifactsRepository.record_hub_artifact``."""
        with self._store.write("record_hub_artifact") as conn:
            lock_chunk_row(conn, chunk_id)
            already = conn.execute(
                self._store.select(s.artifacts, s.artifacts.c.artifact_id).where(
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
                self._store.insert(s.artifacts).values(
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
                enqueue_close_intents(conn, chunk_id, at=at, close_decision=close_decision)
            return True

    def record_landing(
        self,
        chunk_id: str,
        *,
        node_id: str,
        node_name: str,
        epoch: int,
        admission: EpochAdmission,
        repo: str,
        revision: str,
        repository_id: str,
        at: datetime,
        close_decision: CloseEnqueueDecision,
    ) -> bool:
        """See ``src/blizzard/hub/domain/chunk/ports/artifacts.py``'s
        ``IWriteChunkArtifactsRepository.record_landing``."""
        with self._store.write("record_landing") as conn:
            refused, _wrote = write_landing_rows(
                conn,
                chunk_id,
                node_id=node_id,
                node_name=node_name,
                epoch=epoch,
                admission=admission,
                repo=repo,
                revision=revision,
                repository_id=repository_id,
                close_decision=close_decision,
                artifact_id=Id.mint(IdPrefix.ARTIFACT, self._clock).value,
                at=at,
            )
            return refused


_artifacts_conforms: type[IWriteChunkArtifactsRepository] = ChunkArtifactsStore
