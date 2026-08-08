"""The node envelope and the apply-response.

The **envelope** is what a node-step is worked from: the pre-prompt (base prompt + any arrival
addendum, already inlined), the node's config, the chunk's work refs, and every artifact resolved
latest-by-epoch. The **apply-response** is a completion's reply — the next envelope, a signal that a
hub node took over, or a failure."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.hub.domain.graph import Executor, JudgedBy, SessionMode
from blizzard.wire.graph import ProducesEntry


class EnvelopeArtifact(BaseModel):
    """One artifact carried into a node-step, resolved latest-by-epoch."""

    name: str
    kind: ArtifactKind
    node_name: str
    epoch: int
    # git_commit variant
    repo: str | None = None
    branch_name: str | None = None
    commit_hash: str | None = None
    # asset variant
    content: str | None = None


class EnvelopeChoice(BaseModel):
    """A selectable outcome the worker's judgement may emit."""

    name: str
    description: str
    # Whether this choice is gated on green checks (issue #114); `False` leaves it ungated.
    requires_checks: bool = False


class RotatePolicyView(BaseModel):
    """The declared session's rotation bounds (issue #144), carried on ``NodeConfig``.
    ``max_invocations`` counts **harness invocations** — spawn, resume, judge, nudge — not node-steps,
    of which one burns two or three."""

    max_context_tokens: int | None = None
    max_transcript_bytes: int | None = None
    max_invocations: int | None = None


class NodeConfig(BaseModel):
    """The node's invariant identity for this step."""

    node_id: str
    node_name: str
    executor: Executor
    session: SessionMode
    # The session reference target (issues #115, #144); ``None`` means bare ``resume`` or ``fresh``.
    session_source: str | None = None
    # The declared pool this node-step belongs to (issue #144) — ``None`` for a node that names one by
    # node or bare, which carries no pool but still carries the chunk's defaults below.
    session_name: str | None = None
    # The prioritized model preference list and the effort, already merged — opaque preference strings
    # an adapter resolves (`bzh:pluggable-seams`). Empty / ``None`` *expresses no preference*.
    session_model: list[str] = []
    session_effort: str | None = None
    # The pool's rotation bounds; ``None`` when none were authored — nothing bounds the lineage.
    session_rotate: RotatePolicyView | None = None
    judged_by: JudgedBy
    checks: list[str] = []
    # Where this node's checks run, relative to the leased env's workdir, and the per-check
    # timeout (issue #114).
    checks_cwd: str | None = None
    checks_timeout: int | None = None
    produces: list[ProducesEntry] = []
    retries_max: int | None = None
    mode: str | None = None
    choices: list[EnvelopeChoice] = []


class NodeEnvelope(BaseModel):
    """Everything a runner needs to work one node-step."""

    chunk_id: str
    graph_id: str
    epoch: int
    node: NodeConfig
    # The pre-prompt: base prompt + inlined arrival addendum. ``None`` where there is no worker prompt.
    prompt: str | None
    judgement_prompt: str | None
    work_refs: list[dict[str, str]] = []
    artifacts: list[EnvelopeArtifact] = []


class ApplyOutcome(StrEnum):
    """What a completion's apply produced."""

    NEXT = "next"  # the runner continues in place; `next_envelope` is set
    HUB_NODE_TAKEN = "hub_node_taken"  # a hub node (deliver) took over; runner holds envs, waits
    PARKED_AT_GATE = "parked_at_gate"  # a human gate: waiting_on_human (shaped, P7)
    MIGRATED = "migrated"  # a cross-graph migration re-pinned + re-queued the chunk (#90); runner tears down
    DONE = "done"  # the chunk reached the terminal
    FAILURE = "failure"  # stale epoch, terminal chunk, or a rejected submission


class ApplyResponse(BaseModel):
    """The response to a completion submission. Exactly one of ``next_envelope`` (when ``outcome ==
    next``) or ``detail`` (on a non-advancing outcome) is meaningful; ``outcome`` discriminates."""

    outcome: ApplyOutcome
    next_envelope: NodeEnvelope | None = None
    detail: str | None = None
