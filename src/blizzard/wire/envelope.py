"""The node envelope and the apply-response (D-072/D-089/D-090).

The **envelope** is what the runner works a node-step from: the pre-prompt (base
prompt + any arrival addendum, already inlined), the node's config, the chunk's
PM pointers, and every artifact resolved latest-by-epoch (D-036/D-089). It is
handed back by the claim response, by ``POST /chunks/{id}/completions`` (the next
node), and by the idempotent ``GET /chunks/{id}/envelope`` re-read (D-090).

The **apply-response** is the completion's reply: the next envelope, or a signal
that a hub node took over, or a failure — the advancement checkpoint that lets the
runner continue in place (D-072).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.hub.domain.graph import Executor, JudgedBy, SessionMode


class EnvelopeArtifact(BaseModel):
    """One artifact carried into a node-step, resolved latest-by-epoch (D-036)."""

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
    """A selectable outcome the worker's judgement may emit (D-042)."""

    name: str
    description: str


class NodeConfig(BaseModel):
    """The node's invariant identity for this step (D-025/D-038)."""

    node_id: str
    node_name: str
    executor: Executor
    session: SessionMode
    judged_by: JudgedBy
    checks: list[str] = []
    produces: list[str] = []
    retries_max: int | None = None
    mode: str | None = None
    choices: list[EnvelopeChoice] = []


class NodeEnvelope(BaseModel):
    """Everything a runner needs to work one node-step (D-089)."""

    chunk_id: str
    graph_id: str
    epoch: int
    node: NodeConfig
    # The pre-prompt: base prompt + inlined arrival addendum (D-038). None at a
    # hub node or a human gate, which carry no worker prompt.
    prompt: str | None
    judgement_prompt: str | None
    pm_pointers: list[dict[str, str]] = []
    artifacts: list[EnvelopeArtifact] = []


class ApplyOutcome(StrEnum):
    """What a completion's apply produced (D-072)."""

    NEXT = "next"  # the runner continues in place; `next_envelope` is set
    HUB_NODE_TAKEN = "hub_node_taken"  # a hub node (deliver) took over; runner holds envs, waits
    PARKED_AT_GATE = "parked_at_gate"  # a human gate: waiting_on_human (shaped, P7)
    DONE = "done"  # the chunk reached the terminal
    FAILURE = "failure"  # stale epoch, terminal chunk, or a rejected submission


class ApplyResponse(BaseModel):
    """The response to a completion submission (D-072).

    Exactly one of ``next_envelope`` (when ``outcome == next``) or ``detail`` (on a
    non-advancing outcome) is meaningful; the ``outcome`` discriminates.
    """

    outcome: ApplyOutcome
    next_envelope: NodeEnvelope | None = None
    detail: str | None = None
