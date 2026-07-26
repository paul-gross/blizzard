"""The node envelope and the apply-response.

The **envelope** is what the runner works a node-step from: the pre-prompt (base
prompt + any arrival addendum, already inlined), the node's config, the chunk's
work refs, and every artifact resolved latest-by-epoch. It is
handed back by the claim response, by ``POST /chunks/{id}/completions`` (the next
node), and by the idempotent ``GET /chunks/{id}/envelope`` re-read.

The **apply-response** is the completion's reply: the next envelope, or a signal
that a hub node took over, or a failure — the advancement checkpoint that lets the
runner continue in place.
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
    # ``blizzard.hub.domain.graph.Choice.requires_checks``. The runner's local gate reads
    # this off the selected choice; default `False` keeps every existing choice ungated.
    requires_checks: bool = False


class NodeConfig(BaseModel):
    """The node's invariant identity for this step."""

    node_id: str
    node_name: str
    executor: Executor
    session: SessionMode
    # The targeted-resume source node name (issue #115) — see
    # ``blizzard.hub.domain.graph.Node.session_source``. ``None`` means "chunk
    # most-recent" (bare ``resume``) or ``fresh``.
    session_source: str | None = None
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
