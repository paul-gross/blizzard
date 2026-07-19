"""The completion submission — a node-step's atomic, fenced write.

``POST /chunks/{id}/completions`` submits one node-step's completion: the judgement
choice the worker emitted, the check results, and the step's artifacts — **one
atomic, epoch-fenced write**. Git-commit artifacts name branches already
pushed to the forge. A stale epoch is rejected and the artifacts never
enter the store. The reply is the :class:`~blizzard.wire.envelope.ApplyResponse`.

At a human-judged node the hub rejects a plain transition-style completion (human
signoff required) — a runner-config gate posts a decision instead
(:mod:`blizzard.wire.decision`). The one exception is the **resolving transition**:
a completion whose ``decision_id`` points at a resolved decision is how the holding
runner advances a chunk past a gate once a person has decided.
"""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.hub.domain.artifacts import ArtifactKind


class SubmittedArtifact(BaseModel):
    """An artifact committed atomically with the completion."""

    name: str
    kind: ArtifactKind
    # git_commit variant — the branch is pushed to the forge before submission.
    repo: str | None = None
    branch_name: str | None = None
    commit_hash: str | None = None
    # asset variant
    content: str | None = None
    # True when this asset's content came from an explicit `blizzard runner attach`
    # (issue #113) rather than the judgement assessment fallback. Default `False` keeps
    # existing graphs and the regenerated OpenAPI/TS client additive.
    attached: bool = False


def satisfied_produces_names(artifacts: list[SubmittedArtifact]) -> set[str]:
    """The ``produces:`` names this artifact list explicitly satisfies — an artifact
    submitted with ``attached=True`` (an explicit ``blizzard runner attach``), or a
    ``GIT_COMMIT`` artifact, whose pushed branch is itself the explicit signal a name
    covered by a commit needed no separate attach (issue #113). A name present only as
    the judgement-assessment fallback (``attached=False``, kind ``ASSET``) is excluded —
    that is exactly the gap both callers below watch for.

    The **one** shared home for this coverage predicate: the runner's nudge check
    (:func:`~blizzard.runner.loop.steps._missing_produces`) and the hub's produces
    backstop (:func:`~blizzard.hub.domain.produces_auth.check_produces`) both call this
    rather than each re-deriving "covered", so the two cannot drift apart again — the bug
    this function fixes was exactly that drift (the hub backstop rejecting a
    git-commit-covered name the runner's own model already treats as satisfied). Lives
    here, not in either domain package, because ``wire`` is the one module both the hub
    and runner layers already import without crossing each other (``bzh:screaming-
    architecture`` keeps hub and runner as separate top-level packages neither importing
    the other's domain)."""
    return {a.name for a in artifacts if a.attached or a.kind == ArtifactKind.GIT_COMMIT}


class CheckResult(BaseModel):
    """One deterministic check's outcome, informing the verdict."""

    command: str
    passed: bool


class CompletionSubmission(BaseModel):
    """A node-step's completion — judgement choice + checks + artifacts + epoch."""

    choice: str  # the `<Choice>{name}</Choice>` the worker emitted
    epoch: int  # the executing lease's fence, checked against the chunk's latest
    runner_id: str
    from_node_id: str
    check_results: list[CheckResult] = []
    artifacts: list[SubmittedArtifact] = []
    # Set only on a gate-resolving transition: the decision this transition
    # resolves. Its presence is what makes a transition out of a human-judged node
    # legal — a worker transition there (no decision_id) is rejected (human signoff).
    decision_id: str | None = None
    # The route capability token stamped at enqueue (issue #84a) — the plaintext the
    # claim response returned once, presented here so a later authorization check
    # (Phase 6) can confirm the submitter still holds the chunk's live route. Optional
    # in this phase: the hub does not yet reject on it (present-only).
    route_token: str | None = None
