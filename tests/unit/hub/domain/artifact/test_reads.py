from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.artifact.reads import ArtifactReadAmbiguous, resolve_artifact, resolve_artifacts


def test_resolution_and_recency() -> None:
    a = StoredArtifact(ArtifactKind.ASSET, "report", "", None, None, "a", "chunk", "node", "alpha", 1)
    dated = replace(a, artifact_id="b", produced_at=datetime(2026, 1, 1, tzinfo=UTC))
    assert resolve_artifact([]) is None
    assert resolve_artifact([dated, a]) == dated
    assert resolve_artifact([dated, replace(a, epoch=2)]) == replace(a, epoch=2)
    assert resolve_artifacts({"report": [a, dated], "empty": []}) == {"report": dated}


def test_ambiguous_preserves_candidates_and_names_producers_deterministically() -> None:
    a = StoredArtifact(ArtifactKind.ASSET, "report", "", None, None, "a", "chunk", "node", "alpha", 1)
    b = replace(a, artifact_id="b", node_name="beta")
    with pytest.raises(ArtifactReadAmbiguous) as exc:
        resolve_artifact([b, a])
    assert exc.value.candidates == (a, b)
    assert "'alpha', 'beta'" in str(exc.value)
