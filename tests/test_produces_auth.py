"""Produces-artifact authorization (unit tier) — ``check_produces``, node + artifacts
only (issue #113 phase 5).

A pure function of a :class:`Node` plus the submission's own
:class:`~blizzard.wire.completion.SubmittedArtifact` list (``bzh:domain-takes-objects``):
no store, no HTTP, no clock — the same shape ``test_route_auth.py`` holds
``check_route_token`` to.
"""

from __future__ import annotations

import pytest

from blizzard.hub.config import PRODUCES_ENFORCE, PRODUCES_WARN
from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.hub.domain.graph import Executor, JudgedBy, Node, SessionMode
from blizzard.hub.domain.produces_auth import check_produces
from blizzard.wire.completion import SubmittedArtifact

pytestmark = pytest.mark.unit


def _node(*, produces: list[str]) -> Node:
    return Node(
        node_id="nd_build",
        graph_id="gr_1",
        name="build",
        executor=Executor.RUNNER,
        prompt="do the work",
        checks=[],
        produces=produces,
        session=SessionMode.RESUME,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
        mode=None,
    )


def _artifact(name: str, *, attached: bool) -> SubmittedArtifact:
    return SubmittedArtifact(name=name, kind=ArtifactKind.ASSET, content="stuff", attached=attached)


def _git_commit_artifact(name: str) -> SubmittedArtifact:
    return SubmittedArtifact(
        name=name, kind=ArtifactKind.GIT_COMMIT, repo=name, branch_name="b", commit_hash="deadbeef"
    )


def test_a_node_with_no_produces_is_a_clean_no_op() -> None:
    node = _node(produces=[])

    assert check_produces(node, [], mode=PRODUCES_ENFORCE) is None
    assert check_produces(node, [], mode=PRODUCES_WARN) is None


def test_every_produces_name_explicitly_attached_passes_under_both_modes() -> None:
    node = _node(produces=["notes", "diary"])
    artifacts = [_artifact("notes", attached=True), _artifact("diary", attached=True)]

    assert check_produces(node, artifacts, mode=PRODUCES_ENFORCE) is None
    assert check_produces(node, artifacts, mode=PRODUCES_WARN) is None


def test_a_missing_name_is_rejected_under_enforce() -> None:
    node = _node(produces=["notes"])

    detail = check_produces(node, [], mode=PRODUCES_ENFORCE)

    assert detail is not None
    assert "notes" in detail


def test_a_fallback_only_name_attached_false_is_rejected_under_enforce() -> None:
    """A name present but only as the judgement-assessment fallback (``attached=False``)
    still counts as lacking an explicit attachment — the exact criterion this check owes
    issue #113 criterion 6."""
    node = _node(produces=["notes"])
    artifacts = [_artifact("notes", attached=False)]

    detail = check_produces(node, artifacts, mode=PRODUCES_ENFORCE)

    assert detail is not None
    assert "notes" in detail


def test_missing_names_are_all_named_in_the_rejection_detail() -> None:
    node = _node(produces=["notes", "diary", "summary"])
    artifacts = [_artifact("diary", attached=True), _artifact("summary", attached=False)]

    detail = check_produces(node, artifacts, mode=PRODUCES_ENFORCE)

    assert detail is not None
    assert "notes" in detail
    assert "summary" in detail
    assert "diary" not in detail


def test_a_git_commit_covered_produces_name_is_accepted_under_enforce() -> None:
    """A `produces:` name legitimately covered by a pushed git commit (the runner's own
    coverage model, `runner/loop/steps.py`'s `_missing_produces` /
    `_collect_asset_artifacts`) carries `attached=False` on its `GIT_COMMIT`
    `SubmittedArtifact` — this must not be rejected as an unattached name; the two
    coverage models share `~blizzard.wire.completion.satisfied_produces_names` so they
    cannot disagree."""
    node = _node(produces=["backend"])
    artifacts = [_git_commit_artifact("backend")]

    assert check_produces(node, artifacts, mode=PRODUCES_ENFORCE) is None
    assert check_produces(node, artifacts, mode=PRODUCES_WARN) is None


def test_an_asset_fallback_name_is_still_rejected_even_alongside_a_covered_git_name() -> None:
    """A `GIT_COMMIT` artifact covering one name must not accidentally cover an
    unrelated `ASSET` name that only carries the assessment fallback."""
    node = _node(produces=["backend", "findings"])
    artifacts = [_git_commit_artifact("backend"), _artifact("findings", attached=False)]

    detail = check_produces(node, artifacts, mode=PRODUCES_ENFORCE)

    assert detail is not None
    assert "findings" in detail
    assert "backend" not in detail


def test_warn_mode_never_rejects_regardless_of_failure() -> None:
    node = _node(produces=["notes"])

    detail = check_produces(node, [], mode=PRODUCES_WARN)

    assert detail is None
