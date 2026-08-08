"""The hub backstop and the runner nudge agree on produces-coverage (issues #113, #143).

Both must call the one shared :class:`~blizzard.wire.completion.Coverage` rather
than each re-derive "covered" inline. Drives both predicates over one scenario matrix and
asserts they agree on the expected verdict.
"""

from __future__ import annotations

import pytest

from blizzard.hub.config import PRODUCES_ENFORCE
from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.hub.domain.graph import Executor, JudgedBy, Node, ProducesSpec, SessionMode
from blizzard.hub.domain.produces_auth import Produces
from blizzard.runner.loop.produces import ProducesReconciler
from blizzard.wire.completion import SubmittedArtifact
from blizzard.wire.graph import ProducesEntry

from .runner_fakes import make_envelope

pytestmark = pytest.mark.unit


def _node(*, produces: list[str | ProducesSpec]) -> Node:
    return Node(
        node_id="nd_build",
        graph_id="gr_1",
        name="build",
        executor=Executor.RUNNER,
        prompt="do the work",
        checks=[],
        produces=[p if isinstance(p, ProducesSpec) else ProducesSpec(name=p) for p in produces],
        session=SessionMode.RESUME,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
        mode=None,
    )


def _git_commit_spec(name: str = "commit") -> ProducesSpec:
    """A ``{kind: git_commit}`` expectation — the D1 mapping form, as ``build`` nodes
    author it (``name`` defaults to ``commit``, the packaged graphs' own convention, but
    is never what a real git-commit artifact is named)."""
    return ProducesSpec(name=name, kind=ArtifactKind.GIT_COMMIT)


def _git_commit(name: str) -> SubmittedArtifact:
    return SubmittedArtifact(
        name=name, kind=ArtifactKind.GIT_COMMIT, repo=name, branch_name="b", commit_hash="deadbeef"
    )


def _asset(name: str, *, attached: bool) -> SubmittedArtifact:
    return SubmittedArtifact(name=name, kind=ArtifactKind.ASSET, content="stuff", attached=attached)


#: (id, produces, submission artifacts, expected "is every name covered?"). A ``produces``
#: entry is either a bare asset name (``str``) or a :func:`_git_commit_spec` (issue #143, D2).
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
    # --- git_commit-kind expectations (issue #143, D2): kind match, not name match. ---
    (
        "git-commit-kind-covered-by-a-repo-named-artifact",
        [_git_commit_spec()],
        [_git_commit("toy-api")],  # named after the repo, never the declared name "commit"
        True,
    ),
    ("git-commit-kind-with-zero-commits-is-unmet", [_git_commit_spec()], [], False),
    (
        "git-commit-kind-not-satisfied-by-an-asset-of-the-same-name",
        [_git_commit_spec()],
        [_asset("commit", attached=True)],
        False,
    ),
    (
        "git-commit-kind-plus-asset-kind-both-covered",
        [_git_commit_spec(), "findings"],
        [_git_commit("toy-api"), _asset("findings", attached=True)],
        True,
    ),
    (
        "git-commit-kind-covered-but-asset-kind-uncovered",
        [_git_commit_spec(), "findings"],
        [_git_commit("toy-api")],
        False,
    ),
    (
        "multi-repo-git-commit-kind-covered-by-either-one",
        [_git_commit_spec()],
        [_git_commit("blizzard")],
        True,
    ),
]


def _envelope_produces(produces: list[str | ProducesSpec]) -> list[str | ProducesEntry]:
    """Mirror a ``produces`` scenario entry into :func:`make_envelope`'s own vocabulary —
    a bare name stays a bare name; a :class:`ProducesSpec` (the hub's kind-carrying type)
    becomes the wire's :class:`ProducesEntry` counterpart, same ``name``/``kind``."""
    return [p if isinstance(p, str) else ProducesEntry(name=p.name, kind=p.kind) for p in produces]


@pytest.mark.parametrize(
    ("produces", "artifacts", "all_covered"),
    [pytest.param(p, a, c, id=i) for i, p, a, c in _SCENARIOS],
)
def test_hub_and_runner_agree_on_coverage(
    produces: list[str | ProducesSpec], artifacts: list[SubmittedArtifact], all_covered: bool
) -> None:
    """One scenario, both predicates, same verdict — and the verdict is the expected one:
    two sides re-forked into the same wrong answer would still agree with each other."""
    hub_rejects = Produces(_node(produces=produces), artifacts).rejection(mode=PRODUCES_ENFORCE) is not None
    envelope = make_envelope(
        "ch_1", "build", node_id="nd_build", choices=[("pass", "ok")], produces=_envelope_produces(produces)
    )
    runner_nudges = bool(ProducesReconciler(envelope).missing(artifacts, {}))

    assert hub_rejects == runner_nudges, (
        f"produces-coverage drift: the hub backstop {'rejects' if hub_rejects else 'accepts'} this "
        f"submission while the runner {'would nudge' if runner_nudges else 'is satisfied'} — the two "
        f"must share `Coverage`, so a worker that satisfies the runner is never "
        f"fenced out by the hub (issue #143)."
    )
    assert hub_rejects is not all_covered
    assert runner_nudges is not all_covered


def test_a_git_commit_covered_name_never_nudges_the_worker() -> None:
    """The runner half of the regression, pinned on its own (issue #143): a committed
    and declared ``produces:`` name must not provoke a nudge."""
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")], produces=["backend"])

    assert ProducesReconciler(envelope).missing([_git_commit("backend")], {}) == []


def test_a_runner_local_attachment_covers_the_name_without_any_artifact() -> None:
    """The runner also honours its own local attachment store, pinned so the attachment
    path is not mistaken for part of the shared predicate."""
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")], produces=["findings"])

    assert ProducesReconciler(envelope).missing([], {"findings": "the findings"}) == []


def test_a_git_commit_kind_expectation_is_covered_by_kind_not_by_name() -> None:
    """The runner half of the D2 kind-match rule, pinned on its own (issue #143): a
    ``git_commit`` spec is met by kind, not by a name no real artifact ever carries."""
    envelope = make_envelope(
        "ch_1",
        "build",
        node_id="nd_build",
        choices=[("pass", "ok")],
        produces=[ProducesEntry(name="commit", kind=ArtifactKind.GIT_COMMIT)],
    )

    assert ProducesReconciler(envelope).missing([_git_commit("toy-api")], {}) == []


def test_a_git_commit_kind_expectation_with_zero_commits_nudges_the_worker() -> None:
    """The runner's other D2 half: zero ``GIT_COMMIT`` artifacts leaves a ``git_commit``
    spec missing — nudge-worthy — exactly as a zero-attachment asset spec is today."""
    envelope = make_envelope(
        "ch_1",
        "build",
        node_id="nd_build",
        choices=[("pass", "ok")],
        produces=[ProducesEntry(name="commit", kind=ArtifactKind.GIT_COMMIT)],
    )

    missing = ProducesReconciler(envelope).missing([], {})
    assert [spec.name for spec in missing] == ["commit"]
    assert missing[0].kind is ArtifactKind.GIT_COMMIT
