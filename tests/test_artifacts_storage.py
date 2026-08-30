"""Artifact storage-model round trip (unit tier) — the flat-row compression.

Every typed artifact must compress losslessly to its flat storage row and
uncompress back to the same variant. Also pins the store key ``{node}.{name}.{epoch}``.
"""

from __future__ import annotations

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.hub.domain.artifacts import ArtifactRow, AssetArtifact, GitCommitArtifact, Provenance

pytestmark = pytest.mark.unit

_PROV = Provenance(chunk_id="ch_x", node_id="nd_build_1", epoch=7)


def test_git_commit_round_trips_exactly_with_forge() -> None:
    """The worker-declared ``forge`` (issue #143, Phase 4) round-trips through the
    flat storage row exactly like ``repo`` — a ``git_commit``-only sibling column,
    not folded into ``data``."""
    art = GitCommitArtifact(
        artifact_id="art_1",
        name="patch",
        produced_by=_PROV,
        repo="blizzard",
        branch_name="feature/ask-timeout",
        commit_hash="9f3c2ab",
        forge="file:///origins/blizzard.git",
    )
    row = ArtifactRow.of(art, node_name="build")
    assert row.data == "feature/ask-timeout:9f3c2ab"
    assert row.repo == "blizzard"
    assert row.forge == "file:///origins/blizzard.git"
    assert row.artifact == art


def test_git_commit_row_with_no_forge_reads_back_as_empty() -> None:
    """A pre-Phase-4 row this column predates carries ``forge=None`` — it reads back
    as ``""`` (`bzh:facts-not-status` — no separate "unknown" sentinel), the same
    tolerance a legacy-null ``repo`` already gets."""
    row = ArtifactRow.of(
        GitCommitArtifact(
            artifact_id="art_1",
            name="patch",
            produced_by=_PROV,
            repo="blizzard",
            branch_name="feature/ask-timeout",
            commit_hash="9f3c2ab",
        ),
        node_name="build",
    )
    assert row.forge is None  # the empty-string default compresses to a null column
    art = row.artifact
    assert isinstance(art, GitCommitArtifact)
    assert art.forge == ""
    assert art.kind is ArtifactKind.GIT_COMMIT


def test_asset_round_trips_exactly() -> None:
    art = AssetArtifact(
        artifact_id="art_2",
        name="review-findings",
        produced_by=_PROV,
        content="two blocking issues",
    )
    row = ArtifactRow.of(art, node_name="review")
    assert row.data == "two blocking issues"
    assert row.repo is None
    assert row.artifact == art


def test_store_key_uses_node_name_not_id() -> None:
    art = AssetArtifact(artifact_id="art_2", name="review-findings", produced_by=_PROV, content="x")
    row = ArtifactRow.of(art, node_name="review")
    assert row.store_key == "review.review-findings.7"
