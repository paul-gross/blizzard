"""The hub backstop and the runner nudge agree on produces-coverage (unit tier, issue #113).

The bug this guard closes was a *disagreement*, not a wrong answer on either side alone:
the hub's :func:`~blizzard.hub.domain.produces_auth.check_produces` counted a ``produces:``
name covered only by ``attached=True``, so a name legitimately satisfied by a pushed git
commit (whose ``SubmittedArtifact`` carries ``attached=False``) was **rejected** under
``produces_mode=enforce`` — while the runner's own
:func:`~blizzard.runner.loop.steps._missing_produces` already treated that same name as
satisfied and never nudged for it. A worker could therefore do exactly what the runner
asked of it and still have its completion fenced out by the hub.

Both now call :func:`~blizzard.wire.completion.satisfied_produces_names`. That shared call
is easy to un-share again — a future edit re-deriving "covered" inline on either side would
restore the drift silently, because each side's own tests would still pass. This module is
the guard against that: it drives **both** predicates over one scenario matrix and asserts
they return the same verdict for every scenario, so a re-fork fails here rather than in
production under ``enforce``.

Distinct from ``test_produces_auth.py`` (the hub predicate's own behaviour) and
``test_runner_nudge.py`` (the nudge's loop behaviour) — neither of those can observe a
disagreement, since each sees only one side.
"""

from __future__ import annotations

import pytest

from blizzard.hub.config import PRODUCES_ENFORCE
from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.hub.domain.graph import Executor, JudgedBy, Node, SessionMode
from blizzard.hub.domain.produces_auth import check_produces
from blizzard.runner.loop.steps import _missing_produces
from blizzard.wire.completion import SubmittedArtifact

from .runner_fakes import make_envelope

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


def _git_commit(name: str) -> SubmittedArtifact:
    return SubmittedArtifact(
        name=name, kind=ArtifactKind.GIT_COMMIT, repo=name, branch_name="b", commit_hash="deadbeef"
    )


def _asset(name: str, *, attached: bool) -> SubmittedArtifact:
    return SubmittedArtifact(name=name, kind=ArtifactKind.ASSET, content="stuff", attached=attached)


#: (id, produces, submission artifacts, expected "is every name covered?").
#: ``attachments`` is left empty throughout so both sides read the *same* evidence: a
#: runner-local attachment reaches the hub as an ``attached=True`` artifact in the very
#: submission below, which the matrix models directly.
_SCENARIOS = [
    ("no-produces", [], [], True),
    ("git-commit-covers-the-name", ["backend"], [_git_commit("backend")], True),
    ("explicit-attach-covers-the-name", ["findings"], [_asset("findings", attached=True)], True),
    ("assessment-fallback-does-not-cover", ["findings"], [_asset("findings", attached=False)], False),
    ("nothing-submitted-at-all", ["findings"], [], False),
    (
        "mixed-git-covered-plus-uncovered-fallback",
        ["backend", "findings"],
        [_git_commit("backend"), _asset("findings", attached=False)],
        False,
    ),
    (
        "mixed-all-covered-by-different-means",
        ["backend", "findings"],
        [_git_commit("backend"), _asset("findings", attached=True)],
        True,
    ),
    ("an-unrelated-artifact-covers-nothing", ["findings"], [_git_commit("backend")], False),
]


@pytest.mark.parametrize(
    ("produces", "artifacts", "all_covered"),
    [pytest.param(p, a, c, id=i) for i, p, a, c in _SCENARIOS],
)
def test_hub_and_runner_agree_on_coverage(
    produces: list[str], artifacts: list[SubmittedArtifact], all_covered: bool
) -> None:
    """One scenario, both predicates, same verdict — and the verdict is the expected one.

    Asserting against ``all_covered`` as well as against each other matters: two sides that
    re-forked into the *same* wrong answer would agree with each other and still be broken.
    """
    hub_rejects = check_produces(_node(produces=produces), artifacts, mode=PRODUCES_ENFORCE) is not None
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")], produces=produces)
    runner_nudges = bool(_missing_produces(envelope, artifacts, {}))

    assert hub_rejects == runner_nudges, (
        f"produces-coverage drift: the hub backstop {'rejects' if hub_rejects else 'accepts'} this "
        f"submission while the runner {'would nudge' if runner_nudges else 'is satisfied'} — the two "
        f"must share `satisfied_produces_names`, so a worker that satisfies the runner is never "
        f"fenced out by the hub (issue #113)."
    )
    assert hub_rejects is not all_covered
    assert runner_nudges is not all_covered


def test_a_git_commit_covered_name_never_nudges_the_worker() -> None:
    """The runner half of the regression, pinned on its own.

    ``_push_and_collect_artifacts`` only ever builds ``GIT_COMMIT`` artifacts, so this is
    the shape a committed-and-pushed ``produces:`` name actually arrives in. It must not
    provoke a nudge — the worker already produced the thing the graph asked for.
    """
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")], produces=["backend"])

    assert _missing_produces(envelope, [_git_commit("backend")], {}) == []


def test_a_runner_local_attachment_covers_the_name_without_any_artifact() -> None:
    """The runner also honours its own local attachment store, which the hub never sees
    directly — it reaches the hub as the ``attached=True`` artifact assembly builds from it.
    Pinned so the attachment path is not mistaken for part of the shared predicate.
    """
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")], produces=["findings"])

    assert _missing_produces(envelope, [], {"findings": "the findings"}) == []
