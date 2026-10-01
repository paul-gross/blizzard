"""Commit-pointer validation (unit tier) — a `git_commit` artifact must name a repo, a
branch with no `:`, and a full lowercase 40- or 64-hex hash; an asset is never judged."""

from __future__ import annotations

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.hub.domain.commit_pointer import CommitPointerPolicy
from blizzard.wire.completion import SubmittedArtifact

pytestmark = pytest.mark.unit

_SHA1 = "a" * 40
_SHA256 = "b" * 64


def _commit(**overrides: str | None) -> SubmittedArtifact:
    fields = {"repo": "acme/widget", "branch_name": "feat/x", "commit_hash": _SHA1, **overrides}
    return SubmittedArtifact.model_validate({"name": "w", "kind": ArtifactKind.GIT_COMMIT, **fields})


def test_a_complete_pointer_is_accepted() -> None:
    assert CommitPointerPolicy([_commit()]).rejection() is None
    assert CommitPointerPolicy([_commit(commit_hash=_SHA256, forge="https://example.test/acme")]).rejection() is None


def test_no_artifacts_is_accepted() -> None:
    assert CommitPointerPolicy([]).rejection() is None


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"repo": None}, "repo"),
        ({"repo": "  "}, "repo"),
        ({"branch_name": None}, "branch_name"),
        ({"branch_name": ""}, "branch_name"),
        ({"branch_name": "a:b"}, "branch_name"),
        ({"commit_hash": None}, "commit_hash"),
        ({"commit_hash": "abc123"}, "commit_hash"),
        ({"commit_hash": "A" * 40}, "commit_hash"),
        ({"commit_hash": "a" * 41}, "commit_hash"),
        ({"commit_hash": "g" * 40}, "commit_hash"),
    ],
)
def test_each_missing_or_malformed_field_is_refused_by_name(overrides: dict[str, str | None], field: str) -> None:
    detail = CommitPointerPolicy([_commit(**overrides)]).rejection()
    assert detail is not None
    assert "`w`" in detail and f"`{field}`" in detail


def test_an_asset_artifact_is_ignored() -> None:
    asset = SubmittedArtifact(name="notes", kind=ArtifactKind.ASSET, content="diff --git a/x b/x")
    assert CommitPointerPolicy([asset]).rejection() is None
