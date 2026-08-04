"""The node envelope and the apply-response.

The **envelope** is what the runner works a node-step from: the pre-prompt (base
prompt + any arrival addendum, already inlined), the node's config, the chunk's
work refs, and every artifact resolved latest-by-epoch. It is
handed back by the claim response, by ``POST /chunks/{id}/completions`` (the next
node), and by the idempotent ``GET /chunks/{id}/envelope`` re-read.

The **apply-response** is the completion's reply: the next envelope, or a signal
that a hub node took over, or a failure — the advancement checkpoint.
"""

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
    # Whether this choice is gated on green checks (issue #114) — see
    # ``blizzard.hub.domain.graph.Choice.requires_checks``. Default `False` leaves a
    # choice ungated.
    requires_checks: bool = False


class RotatePolicyView(BaseModel):
    """The declared session's rotation bounds (issue #144).

    The wire counterpart of :class:`~blizzard.hub.domain.graph.RotatePolicy`, carried on
    :class:`NodeConfig`. ``max_invocations`` counts **harness invocations** (spawn, resume,
    judge, nudge), not node-steps — one node-step burns two or three of them."""

    max_context_tokens: int | None = None
    max_transcript_bytes: int | None = None
    max_invocations: int | None = None


class NodeConfig(BaseModel):
    """The node's invariant identity for this step."""

    node_id: str
    node_name: str
    executor: Executor
    session: SessionMode
    # The session reference target (issues #115, #144) — see
    # ``blizzard.hub.domain.graph.Node.session_source``. ``None`` means "chunk
    # most-recent" (bare ``resume``) or bare ``fresh``.
    session_source: str | None = None
    # The **effective** session declaration for this node-step (issue #144), resolved
    # hub-side because the hub owns both halves of the precedence it settles: a graph's
    # `sessions:` declaration over the chunk's own defaults, field by field.
    #
    # ``session_name`` is the declared pool this node-step belongs to. ``None`` for a node
    # whose `session:` names a NODE (`resume:<node>`) or is bare: those carry no pool, but
    # still carry the chunk's defaults below, which is the precedence rule's intended
    # reach.
    session_name: str | None = None
    # The prioritized model preference list and the effort value, already merged. Both are
    # opaque preference strings — a `blizzard:` tier alias or a harness-native name — that
    # the runner's adapter resolves against its own config (`bzh:pluggable-seams`). Empty /
    # ``None`` means *express no preference*: the runner's own default applies.
    session_model: list[str] = []
    session_effort: str | None = None
    # The pool's rotation bounds, ``None`` when the declaration authored none (or the node
    # references no declaration at all) — nothing bounds the lineage.
    session_rotate: RotatePolicyView | None = None
    judged_by: JudgedBy
    checks: list[str] = []
    # Where the runner runs this node's checks and the per-check timeout (issue #114) —
    # see ``blizzard.hub.domain.graph.Node.checks_cwd`` / ``checks_timeout``. The runner
    # resolves ``checks_cwd`` relative to the leased env's binding workdir.
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
    # The pre-prompt: base prompt + inlined arrival addendum. None at a
    # hub node or a human gate, which carry no worker prompt.
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
    """The response to a completion submission.

    Exactly one of ``next_envelope`` (when ``outcome == next``) or ``detail`` (on a
    non-advancing outcome) is meaningful; the ``outcome`` discriminates.
    """

    outcome: ApplyOutcome
    next_envelope: NodeEnvelope | None = None
    detail: str | None = None
