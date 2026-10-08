"""The envelope a node-step is worked from, as the runner reads it.

The pre-prompt (base prompt plus any arrival addendum, already inlined), the node's config, the
chunk's work refs, the node-scope artifacts resolved latest-by-epoch, and the mint's graph-scope
declarations."""

from __future__ import annotations

from dataclasses import dataclass, field

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class ProducesSpec:
    """One node's ``produces:`` expectation, kind-carrying."""

    name: str
    kind: ArtifactKind = ArtifactKind.ASSET


@domain_model
@dataclass(frozen=True)
class RotateBounds:
    """One declared session's rotation bounds — each optional; ``max_invocations`` counts
    harness invocations, not node-steps."""

    max_context_tokens: int | None = None
    max_transcript_bytes: int | None = None
    max_invocations: int | None = None


@domain_model
@dataclass(frozen=True)
class Choice:
    """A selectable outcome the worker's judgement may emit; ``requires_checks`` gates it on
    green checks."""

    name: str
    description: str
    requires_checks: bool = False


@domain_model
@dataclass(frozen=True)
class EnvelopeNode:
    """The node's invariant identity for this step, its session already resolved."""

    node_id: str
    node_name: str
    executor: Executor
    session: SessionMode
    judged_by: JudgedBy
    # The session reference target; ``None`` means bare ``resume`` or ``fresh``.
    session_source: str | None = None
    # The declared pool this step belongs to — ``None`` for a node that names one by node or bare.
    session_name: str | None = None
    # The prioritized model preferences and the effort — opaque strings an adapter resolves
    # (`bzh:pluggable-seams`); empty or ``None`` expresses no preference.
    session_model: list[str] = field(default_factory=list)
    session_effort: str | None = None
    # The acceptable harness set, `session_model`'s shape.
    session_harnesses: list[str] = field(default_factory=list)
    session_rotate: RotateBounds | None = None
    session_compaction_window: str | None = None
    checks: list[str] = field(default_factory=list)
    # Where the checks run, relative to the leased env's workdir, and the per-check timeout.
    checks_cwd: str | None = None
    checks_timeout: int | None = None
    produces: list[ProducesSpec] = field(default_factory=list)
    retries_max: int | None = None
    choices: list[Choice] = field(default_factory=list)


@domain_model
@dataclass(frozen=True)
class CarriedArtifact:
    """One node-scope artifact carried into the step, resolved latest-by-epoch: a
    ``git_commit``'s repo, branch, and commit, or an ``asset``'s content."""

    name: str
    kind: ArtifactKind
    node_name: str
    epoch: int
    repo: str | None = None
    branch_name: str | None = None
    commit_hash: str | None = None
    content: str | None = None


@domain_model
@dataclass(frozen=True)
class GraphArtifact:
    """One graph-scoped artifact baked into the mint, its content inlined."""

    name: str
    kind: ArtifactKind
    content: str


@domain_model
@dataclass(frozen=True)
class Envelope:
    """Everything needed to work one node-step. ``prompt`` is ``None`` where there is no worker
    prompt; each work ref carries ``source`` and ``ref``, plus a source-native ``label`` when
    a configured source renders one."""

    chunk_id: str
    graph_id: str
    epoch: int
    node: EnvelopeNode
    prompt: str | None
    judgement_prompt: str | None
    # ``None`` from a hub that predates the field.
    graph_name: str | None = None
    work_refs: list[dict[str, str]] = field(default_factory=list)
    artifacts: list[CarriedArtifact] = field(default_factory=list)
    graph_artifacts: list[GraphArtifact] = field(default_factory=list)
