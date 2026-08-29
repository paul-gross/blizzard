"""Artifact domain — a chunk's durable outputs and their storage model.

A discriminated union: code works with the typed variants (:class:`GitCommitArtifact`,
:class:`AssetArtifact`), which compress to and uncompress from the single-string
:class:`ArtifactRow` at the store boundary, exactly in both directions.
Dependency-free (``bzh:domain-core``): no SQLAlchemy here."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class ArtifactKind(StrEnum):
    """The union discriminator."""

    GIT_COMMIT = "git_commit"
    ASSET = "asset"


class ArtifactScope(StrEnum):
    """Where an artifact is pinned — a chunk's node-step, the graph mint that baked it
    into the graph itself (``artifacts:``), or blizzard's own published, global-namespace
    documents, resolved at call time (``system``)."""

    NODE = "node"
    GRAPH = "graph"
    SYSTEM = "system"


# One conservative URL path segment — no `/`, since the consuming route percent-encodes a
# bare name into it, so a `/` would reach it as a real separator. Shared by both name
# grammars below: a graph-artifact name is exactly one segment, a system-artifact name a
# `/`-separated path of them, but neither owns the segment shape independently of the other.
_ARTIFACT_NAME_SEGMENT = r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*"

_GRAPH_ARTIFACT_NAME = re.compile(rf"^{_ARTIFACT_NAME_SEGMENT}$")

# A `/`-separated path of that same segment shape — blizzard's own global namespace, so a
# published document can be grouped (`garden/finding-format`) unlike a graph-authored name.
_SYSTEM_ARTIFACT_NAME = re.compile(rf"^{_ARTIFACT_NAME_SEGMENT}(?:/{_ARTIFACT_NAME_SEGMENT})*$")


def is_valid_graph_artifact_name(name: str) -> bool:
    """The single owner of graph-artifact name validity (``canon:one-owner``) — a
    `produces:` name is a different, unvalidated namespace."""
    return bool(_GRAPH_ARTIFACT_NAME.fullmatch(name))


def is_valid_system_artifact_name(name: str) -> bool:
    """The single owner of system-artifact name validity (``canon:one-owner``) — a sibling
    to :func:`is_valid_graph_artifact_name` in blizzard's own global namespace, where a `/`
    groups a name rather than being forbidden."""
    return bool(_SYSTEM_ARTIFACT_NAME.fullmatch(name))


@dataclass(frozen=True)
class Provenance:
    """Where an artifact came from — a reference to its committing transition."""

    chunk_id: str
    node_id: str
    epoch: int


@dataclass(frozen=True)
class GitCommitArtifact:
    """A branch pushed to the forge before submission, pinned by commit hash.

    ``forge`` is the declared origin; ``""`` reads back as "the repo's origin"."""

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

    ``data`` is keyed by ``kind``: ``git_commit`` -> ``<branch>:<commit>``, ``asset``
    -> the raw content. ``repo``/``forge`` are ``git_commit``-only sibling columns."""

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

    @classmethod
    def of(cls, artifact: Artifact, *, node_name: str) -> ArtifactRow:
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
            return cls(
                kind=ArtifactKind.GIT_COMMIT,
                data=f"{artifact.branch_name}:{artifact.commit_hash}",
                repo=artifact.repo,
                forge=artifact.forge or None,
                **common,
            )
        return cls(kind=ArtifactKind.ASSET, data=artifact.content, repo=None, forge=None, **common)

    @property
    def artifact(self) -> Artifact:
        """Uncompress back to the typed artifact (lossless)."""
        provenance = Provenance(chunk_id=self.chunk_id, node_id=self.node_id, epoch=self.epoch)
        if self.kind is ArtifactKind.GIT_COMMIT:
            branch_name, _, commit_hash = self.data.partition(":")
            return GitCommitArtifact(
                artifact_id=self.artifact_id,
                name=self.name,
                produced_by=provenance,
                repo=self.repo or "",
                branch_name=branch_name,
                commit_hash=commit_hash,
                forge=self.forge or "",
            )
        return AssetArtifact(
            artifact_id=self.artifact_id,
            name=self.name,
            produced_by=provenance,
            content=self.data,
        )

    @property
    def store_key(self) -> str:
        """The chunk artifact-store key ``{node}.{artifact-name}.{epoch}``."""
        return f"{self.node_name}.{self.name}.{self.epoch}"
