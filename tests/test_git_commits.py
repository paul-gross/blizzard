"""``GitCommits.of`` skips non-``git_commit`` artifacts wherever they sit in the list."""

from __future__ import annotations

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.hub.delivery.hub_node import GitCommits, UnconvergedDeliveryError
from blizzard.hub.domain.artifact.model import StoredArtifact

pytestmark = pytest.mark.unit


def _asset(name: str = "notes") -> StoredArtifact:
    return StoredArtifact(
        kind=ArtifactKind.ASSET,
        name=name,
        data="free text",
        repo=None,
        forge=None,
        artifact_id=f"art_{name}",
        chunk_id="ch1",
        node_id="n1",
        node_name="build",
        epoch=1,
    )


def _commit(branch: str, *, epoch: int = 1, repo: str = "acme/widget") -> StoredArtifact:
    return StoredArtifact(
        kind=ArtifactKind.GIT_COMMIT,
        name=f"w-{branch}",
        data=f"{branch}:{'a' * 40}",
        repo=repo,
        forge=None,
        artifact_id=f"art_{branch}_{epoch}",
        chunk_id="ch1",
        node_id="n1",
        node_name="build",
        epoch=epoch,
    )


def test_a_commit_listed_after_an_asset_is_still_resolved() -> None:
    commit = _commit("feat/x")

    assert GitCommits.of([_asset(), commit]).rows == [commit]


def test_assets_between_commits_do_not_hide_the_later_one() -> None:
    first, second = _commit("feat/x", repo="acme/widget"), _commit("feat/y", repo="acme/gadget")

    assert GitCommits.of([first, _asset(), second]).rows == [first, second]


def test_two_branches_at_one_epoch_still_raise_past_a_leading_asset() -> None:
    with pytest.raises(UnconvergedDeliveryError):
        GitCommits.of([_asset(), _commit("feat/x"), _commit("feat/y")])
