"""The completion submission — a node-step's atomic, fenced write.

``POST /chunks/{id}/completions`` submits one node-step's completion: the judgement
choice, the check results, and the step's artifacts — **one atomic, epoch-fenced
write**. A stale epoch is rejected and the artifacts never enter the store.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel

from blizzard.hub.domain.artifacts import ArtifactKind


class SubmittedArtifact(BaseModel):
    """An artifact committed atomically with the completion."""

    name: str
    kind: ArtifactKind
    # git_commit variant — the branch is pushed to the forge before submission. `forge` is
    # the worker's own declared origin (issue #143), verified by the runner before it rides.
    forge: str | None = None
    repo: str | None = None
    branch_name: str | None = None
    commit_hash: str | None = None
    # asset variant
    content: str | None = None
    # True when this asset's content came from an explicit attach (issue #113) rather
    # than the judgement assessment fallback.
    attached: bool = False


def satisfied_produces_names(artifacts: list[SubmittedArtifact]) -> set[str]:
    """The ``produces:`` names this artifact list explicitly satisfies — an artifact with
    ``attached=True``, or a ``GIT_COMMIT`` artifact (issue #113). A name present only as
    the judgement-assessment fallback is excluded (``test_produces_coverage_agreement``)."""
    return {a.name for a in artifacts if a.attached or a.kind == ArtifactKind.GIT_COMMIT}


class _ProducesLike(Protocol):
    """Structural shape both ``produces:`` spec types share, so
    :func:`produces_coverage` is generic over either."""

    @property
    def name(self) -> str: ...
    @property
    def kind(self) -> ArtifactKind: ...


def produces_coverage[P: _ProducesLike](specs: Sequence[P], artifacts: list[SubmittedArtifact]) -> list[P]:
    """The ``produces:`` specs this artifact list does **not** cover (issue #143, D2).

    An ``asset`` spec is met by an artifact of **its own name** in
    :func:`satisfied_produces_names`; a ``git_commit`` spec is met by **any**
    ``GIT_COMMIT``-kind artifact — a *kind* match, not a name match."""
    covered_names = satisfied_produces_names(artifacts)
    has_git_commit = any(a.kind == ArtifactKind.GIT_COMMIT for a in artifacts)
    unmet: list[P] = []
    for spec in specs:
        if spec.kind == ArtifactKind.GIT_COMMIT:
            if not has_git_commit:
                unmet.append(spec)
        elif spec.name not in covered_names:
            unmet.append(spec)
    return unmet


class CheckResult(BaseModel):
    """One deterministic check's **runner-executed** outcome (issue #114).

    Carries only ``(command, passed)``; the runner's captured ``output_tail``
    deliberately does not ride the wire."""

    command: str
    passed: bool


class _HasPassed(Protocol):
    """The one field :func:`checks_gate_violated` reads, so it is generic over both
    check-result shapes without either importing the other's type."""

    @property
    def passed(self) -> bool: ...


def checks_gate_violated(requires_checks: bool, check_results: Sequence[_HasPassed]) -> bool:
    """``True`` iff a ``requires_checks`` choice is being taken while any check is red
    (issue #114) — the one shared home for the predicate, guarded by
    ``tests/test_checks_gate_agreement.py``. An ungated choice is never violated; a node
    with no checks records none, so the gate is vacuously satisfied."""
    return requires_checks and any(not r.passed for r in check_results)


class CompletionSubmission(BaseModel):
    """A node-step's completion — judgement choice + checks + artifacts + epoch."""

    choice: str  # the `<Choice>{name}</Choice>` the worker emitted
    epoch: int  # the executing lease's fence, checked against the chunk's latest
    runner_id: str
    from_node_id: str
    # The runner-executed check facts (issue #114) — ``(command, passed)`` per command;
    # empty for a node with no ``checks:``.
    check_results: list[CheckResult] = []
    artifacts: list[SubmittedArtifact] = []
    # Set only on a gate-resolving transition. Its presence is what makes a transition
    # out of a human-judged node legal; without it the transition is rejected.
    decision_id: str | None = None
    # The route capability token stamped at enqueue (issue #84a) — evidence the submitter
    # still holds the chunk's live route. Optional: the hub does not reject on it.
    route_token: str | None = None
