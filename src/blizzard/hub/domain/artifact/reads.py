"""Resolve named artifact candidates without silently choosing a producer."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from blizzard.hub.domain.artifact.model import StoredArtifact


def artifact_recency(artifact: StoredArtifact) -> tuple[int, bool, datetime, str, str, str]:
    """A total recency order, including a stable identity for otherwise equal rows."""
    at = artifact.produced_at
    if at is not None and at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return (
        artifact.epoch,
        at is not None,
        at or datetime.min.replace(tzinfo=UTC),
        artifact.artifact_id,
        artifact.chunk_id,
        artifact.node_id,
    )


class ArtifactReadAmbiguous(Exception):
    """Several producers supplied the same named artifact."""

    def __init__(self, name: str, candidates: Sequence[StoredArtifact]) -> None:
        self.name = name
        self.candidates = tuple(sorted(candidates, key=lambda a: (a.node_name, artifact_recency(a))))
        producers = ", ".join(repr(name) for name in sorted({a.node_name for a in self.candidates}))
        super().__init__(f"artifact {name!r} is ambiguous — produced by {producers}")


def resolve_artifact(candidates: Sequence[StoredArtifact]) -> StoredArtifact | None:
    """Return the newest candidate from the sole producer, or refuse ambiguity."""
    winners: dict[str, StoredArtifact] = {}
    for artifact in candidates:
        previous = winners.get(artifact.node_name)
        if previous is None or artifact_recency(artifact) > artifact_recency(previous):
            winners[artifact.node_name] = artifact
    if len(winners) > 1:
        raise ArtifactReadAmbiguous(candidates[0].name, candidates)
    return next(iter(winners.values()), None)


def resolve_artifacts(candidates: Mapping[str, Sequence[StoredArtifact]]) -> dict[str, StoredArtifact]:
    """Resolve each named candidate list, omitting empty lists."""
    result: dict[str, StoredArtifact] = {}
    for name, items in candidates.items():
        artifact = resolve_artifact(items)
        if artifact is not None:
            result[name] = artifact
    return result
