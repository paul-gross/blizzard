"""Artifact domain — a chunk's durable outputs and their storage model (D-026/D-036).

A discriminated union: code works with the typed variants (:class:`GitCommitArtifact`,
:class:`AssetArtifact`); the compact single-string :class:`ArtifactRow` is the
storage model the variants compress to and uncompress from at the store boundary
(D-036). The round trip is exact in both directions — the property the unit tests
pin.

Dependency-free (``bzh:domain-core``): no SQLAlchemy here. :class:`ArtifactRow` is a
plain dataclass; the store maps it to columns.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ArtifactKind(StrEnum):
    """The union discriminator (D-036)."""

    GIT_COMMIT = "git_commit"
    ASSET = "asset"


@dataclass(frozen=True)
class Provenance:
    """Where an artifact came from — a reference to its committing transition (D-036)."""

    chunk_id: str
    node_id: str
    epoch: int


@dataclass(frozen=True)
class GitCommitArtifact:
    """A branch pushed to the forge before submission, pinned by commit hash (D-026)."""

    artifact_id: str
    name: str
    produced_by: Provenance
    repo: str
    branch_name: str
    commit_hash: str

    kind: ArtifactKind = ArtifactKind.GIT_COMMIT


@dataclass(frozen=True)
class AssetArtifact:
    """A text or blob output — a review's findings, a spike write-up (D-026)."""

    artifact_id: str
    name: str
    produced_by: Provenance
    content: str

    kind: ArtifactKind = ArtifactKind.ASSET


Artifact = GitCommitArtifact | AssetArtifact


@dataclass(frozen=True)
class ArtifactRow:
    """The flat storage row (D-036): variant fields compressed into one ``data`` string.

    ``data`` is keyed by ``kind``: ``git_commit`` -> ``<branch>:<commit>``; ``asset``
    -> the raw content. ``repo`` is a ``git_commit``-only sibling column, not encoded
    in ``data``. The ``{node}`` component of the store key is the node *name*
    (``bzh:facts-not-status`` / D-036); ``node_id`` here is the exact provenance.
    """

    kind: ArtifactKind
    name: str
    data: str
    repo: str | None
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
            **common,
        )
    return ArtifactRow(kind=ArtifactKind.ASSET, data=artifact.content, repo=None, **common)


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
        )
    return AssetArtifact(
        artifact_id=row.artifact_id,
        name=row.name,
        produced_by=provenance,
        content=row.data,
    )


def store_key(row: ArtifactRow) -> str:
    """The chunk artifact-store key ``{node}.{artifact-name}.{epoch}`` (D-036)."""
    return f"{row.node_name}.{row.name}.{row.epoch}"
