"""Artifact domain — a chunk's durable outputs and their storage model.

A discriminated union: code works with the typed variants (:class:`GitCommitArtifact`,
:class:`AssetArtifact`); the compact single-string :class:`ArtifactRow` is the
storage model the variants compress to and uncompress from at the store boundary.
The round trip is exact in both directions — the property the unit tests
pin.

Dependency-free (``bzh:domain-core``): no SQLAlchemy here. :class:`ArtifactRow` is a
plain dataclass; the store maps it to columns.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ArtifactKind(StrEnum):
    """The union discriminator."""

    GIT_COMMIT = "git_commit"
    ASSET = "asset"


@dataclass(frozen=True)
class Provenance:
    """Where an artifact came from — a reference to its committing transition."""

    chunk_id: str
    node_id: str
    epoch: int


@dataclass(frozen=True)
class GitCommitArtifact:
    """A branch pushed to the forge before submission, pinned by commit hash.

    ``forge`` is the worker's own declared origin (issue #143, Phase 4 — decision
    R7), confirmed read-only by the runner's verify before submission; ``""`` only
    for a pre-Phase-4 row this shape predates (a legacy null reads back as "the
    repo's origin" — see :func:`from_row`)."""

    artifact_id: str
    name: str
    produced_by: Provenance
    repo: str
    branch_name: str
    commit_hash: str
    forge: str = ""

    kind: ArtifactKind = ArtifactKind.GIT_COMMIT


@dataclass(frozen=True)
class AssetArtifact:
    """A text or blob output — a review's findings, a spike write-up."""

    artifact_id: str
    name: str
    produced_by: Provenance
    content: str

    kind: ArtifactKind = ArtifactKind.ASSET


Artifact = GitCommitArtifact | AssetArtifact


@dataclass(frozen=True)
class ArtifactRow:
    """The flat storage row: variant fields compressed into one ``data`` string.

    ``data`` is keyed by ``kind``: ``git_commit`` -> ``<branch>:<commit>``; ``asset``
    -> the raw content. ``repo`` and ``forge`` are ``git_commit``-only sibling columns,
    not encoded in ``data`` (``forge`` added issue #143, Phase 4 — a nullable column
    mirroring ``repo``; ``None`` on a legacy pre-Phase-4 row reads back as "the repo's
    origin", :func:`from_row`). The ``{node}`` component of the store key is the node
    *name* (``bzh:facts-not-status``); ``node_id`` here is the exact provenance.
    """

    kind: ArtifactKind
    name: str
    data: str
    repo: str | None
    forge: str | None
    artifact_id: str
    chunk_id: str
    node_id: str
    node_name: str
    epoch: int


def to_row(artifact: Artifact, *, node_name: str) -> ArtifactRow:
    """Compress a typed artifact to its storage row (lossless)."""
    common = {
        "name": artifact.name,
        "artifact_id": artifact.artifact_id,
        "chunk_id": artifact.produced_by.chunk_id,
        "node_id": artifact.produced_by.node_id,
        "node_name": node_name,
        "epoch": artifact.produced_by.epoch,
    }
    if isinstance(artifact, GitCommitArtifact):
        return ArtifactRow(
            kind=ArtifactKind.GIT_COMMIT,
            data=f"{artifact.branch_name}:{artifact.commit_hash}",
            repo=artifact.repo,
            forge=artifact.forge or None,
            **common,
        )
    return ArtifactRow(kind=ArtifactKind.ASSET, data=artifact.content, repo=None, forge=None, **common)


def from_row(row: ArtifactRow) -> Artifact:
    """Uncompress a storage row back to its typed artifact (lossless)."""
    provenance = Provenance(chunk_id=row.chunk_id, node_id=row.node_id, epoch=row.epoch)
    if row.kind is ArtifactKind.GIT_COMMIT:
        branch_name, _, commit_hash = row.data.partition(":")
        return GitCommitArtifact(
            artifact_id=row.artifact_id,
            name=row.name,
            produced_by=provenance,
            repo=row.repo or "",
            branch_name=branch_name,
            commit_hash=commit_hash,
            forge=row.forge or "",
        )
    return AssetArtifact(
        artifact_id=row.artifact_id,
        name=row.name,
        produced_by=provenance,
        content=row.data,
    )


def store_key(row: ArtifactRow) -> str:
    """The chunk artifact-store key ``{node}.{artifact-name}.{epoch}``."""
    return f"{row.node_name}.{row.name}.{row.epoch}"
