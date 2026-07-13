"""Artifact storage-model round trip (unit tier) — the D-036 compression.

Every typed artifact must compress losslessly to its flat storage row and
uncompress back to the same variant. Also pins the store key ``{node}.{name}.{epoch}``.
"""

from __future__ import annotations

import pytest

from blizzard.hub.domain.artifacts import (
    AssetArtifact,
    GitCommitArtifact,
    Provenance,
    from_row,
    store_key,
    to_row,
)

pytestmark = pytest.mark.unit

_PROV = Provenance(chunk_id="ch_x", node_id="nd_build_1", epoch=7)


def test_git_commit_round_trips_exactly() -> None:
    art = GitCommitArtifact(
        artifact_id="art_1",
        name="patch",
        produced_by=_PROV,
        repo="blizzard",
        branch_name="feature/ask-timeout",
        commit_hash="9f3c2ab",
    )
    row = to_row(art, node_name="build")
    assert row.data == "feature/ask-timeout:9f3c2ab"
    assert row.repo == "blizzard"
    assert from_row(row) == art


def test_asset_round_trips_exactly() -> None:
    art = AssetArtifact(
        artifact_id="art_2",
        name="review-findings",
        produced_by=_PROV,
        content="two blocking issues",
    )
    row = to_row(art, node_name="review")
    assert row.data == "two blocking issues"
    assert row.repo is None
    assert from_row(row) == art


def test_store_key_uses_node_name_not_id() -> None:
    art = AssetArtifact(artifact_id="art_2", name="review-findings", produced_by=_PROV, content="x")
    row = to_row(art, node_name="review")
    assert store_key(row) == "review.review-findings.7"
