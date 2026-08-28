"""Workflow-graph domain model — the definition chunks travel.

Two representations: the **authoring doc** (:class:`GraphDoc` and friends), a plain-data
shape carrying no ids, and the **reified graph** (:class:`Graph`, :class:`Node`,
:class:`Choice`, :class:`Edge`), the immutable id-carrying entities a validated doc
compiles into at mint. Every type is dependency-free (``bzh:domain-core``)."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from blizzard import __version__ as HUB_VERSION
from blizzard.hub.domain.artifacts import ArtifactKind


class GraphParseError(ValueError):
    """A graph definition is structurally malformed (before validation)."""


# The reserved terminal a choice may point at instead of a node name.
RESERVED_TERMINAL = "done"

# The cross-graph target prefix (issue #90): a ``to: graph:<name>`` choice re-pins the
# chunk rather than transitioning it (``bzh:migration-not-transition``).
GRAPH_TARGET_PREFIX = "graph:"


@dataclass(frozen=True)
class ChoiceTarget:
    """What a choice's ``to:`` value points at (issue #90) — pure syntax.

    ``node`` a same-graph node or the reserved terminal, ``graph`` a well-formed
    ``graph:<name>``; neither set is :attr:`malformed`."""

    node: str | None = None
    graph: str | None = None

    @classmethod
    def of(cls, to: str) -> ChoiceTarget:
        if not to.startswith(GRAPH_TARGET_PREFIX):
            return cls(node=to)
        name = to[len(GRAPH_TARGET_PREFIX) :]
        if not name or ":" in name:
            return cls()
        return cls(graph=name)

    @property
    def malformed(self) -> bool:
        return self.node is None and self.graph is None


class Executor(StrEnum):
    """Where a node's step runs."""

    RUNNER = "runner"
    HUB = "hub"


class JudgedBy(StrEnum):
    """Who issues a node's exit judgement — the structural gate marker."""

    WORKER = "worker"
    HUMAN = "human"


class SessionMode(StrEnum):
    """Per-node session freshness."""

    RESUME = "resume"
    FRESH = "fresh"


# ``session: resume:<name>`` (issues #115, #144) — resume ``<name>``'s most-recent
# session rather than the chunk's most-recent overall.
SESSION_RESUME_TARGET_PREFIX = "resume:"

# ``session: fresh:<name>`` (issue #144) — mint a session and make it ``<name>``'s new
# head, which a later ``resume:<name>`` member continues.
SESSION_FRESH_TARGET_PREFIX = "fresh:"

# The one owner of the legal-form vocabulary quoted back at an author (``bzh:one-owner``).
SESSION_LEGAL_FORMS = "`fresh`, `resume`, `resume:<node>`, `fresh:<session>`, or `resume:<session>`"


@dataclass(frozen=True)
class SessionRef:
    """What an authored ``session:`` value names (issues #115, #144) — pure syntax.

    ``source`` is the ``<name>`` of a ``resume:``/``fresh:`` form, carried verbatim and
    ``None`` for a bare form; under :attr:`malformed` no field may be relied on."""

    mode: SessionMode
    source: str | None = None
    malformed: bool = False

    @classmethod
    def of(cls, raw: str) -> SessionRef:
        if raw == SessionMode.FRESH.value:
            return cls(SessionMode.FRESH)
        if raw == SessionMode.RESUME.value:
            return cls(SessionMode.RESUME)
        if raw.startswith(SESSION_RESUME_TARGET_PREFIX):
            name = raw[len(SESSION_RESUME_TARGET_PREFIX) :]
            if name:
                return cls(SessionMode.RESUME, name)
        if raw.startswith(SESSION_FRESH_TARGET_PREFIX):
            name = raw[len(SESSION_FRESH_TARGET_PREFIX) :]
            if name:
                return cls(SessionMode.FRESH, name)
        return cls(SessionMode.RESUME, malformed=True)


class RetriesExhausted(StrEnum):
    ESCALATE = "escalate"


# The reserved outcomes a command's exit code maps to absent an explicit choice (#65):
# exit 0 -> success, nonzero -> failure. Machinery-reserved, not authored edges (#67).
HUB_DEFAULT_SUCCESS_CHOICE = "success"
HUB_DEFAULT_FAILURE_CHOICE = "failure"

# The reserved **pending** outcome (#66), signalled on a step's last stdout line at
# exit 0; a nonzero exit is always a failure, never pending.
HUB_PENDING_CHOICE = "pending"

# The default kick-back cap a hub node omitting ``bounce_cap`` tolerates before the
# chunk escalates (#64). Per-node, not global.
DEFAULT_BOUNCE_CAP = 5


@dataclass(frozen=True)
class Parser:
    """One authored mapping, read through the coercions the doc types share.

    Structural coercion only — a shape that cannot be read raises
    :class:`GraphParseError`; whether a readable value is *legal* is the validator's."""

    body: dict[str, object]

    @classmethod
    def of(cls, value: object, where: object) -> Parser:
        if not isinstance(value, dict):
            raise GraphParseError(f"expected a map at {where!r}, got {type(value).__name__}")
        return cls(value)

    def get(self, key: str, default: object = None) -> object:
        return self.body.get(key, default)

    def require(self, key: str, message: str) -> object:
        try:
            return self.body[key]
        except KeyError as exc:
            raise GraphParseError(message) from exc

    def text(self, key: str) -> str | None:
        value = self.body.get(key)
        return str(value) if value is not None else None

    def number(self, key: str) -> int | None:
        value = self.body.get(key)
        return int(str(value)) if value is not None else None

    def items(self, key: str) -> list[object]:
        value = self.body.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            raise GraphParseError(f"expected a list, got {type(value).__name__}")
        return value


# --- Authoring doc (parsed from a YAML body, pre-mint) ----------------------


@dataclass(frozen=True)
class ChoiceDoc:
    """One fused choice/edge entry as authored.

    ``target_graph`` is the raw ``to``'s parsed graph name when it is a well-formed
    cross-graph form; ``model`` overrides the chunk's model on a migration (#90)."""

    name: str
    description: str | None
    to: str | None
    prompt_addendum: str | None = None
    target_graph: str | None = None
    model: str | None = None
    # Gated on green checks (issue #114): unroutable while any of its node's is red.
    requires_checks: bool = False

    @classmethod
    def of(cls, key: object, raw: object) -> ChoiceDoc:
        body = Parser.of(raw, key)
        name = str(key)
        to = body.text("to")
        return cls(
            name=name,
            description=body.text("description"),
            to=to,
            prompt_addendum=body.text("prompt_addendum"),
            target_graph=ChoiceTarget.of(to).graph if to is not None else None,
            model=body.text("model"),
            requires_checks=bool(body.get("requires_checks", False)),
        )


@dataclass(frozen=True)
class JudgementDoc:
    prompt: str | None
    by: JudgedBy
    choices: list[ChoiceDoc]

    @classmethod
    def of(cls, raw: object) -> JudgementDoc | None:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise GraphParseError("`judgement` must be a map")
        body = Parser(raw)
        by = JudgedBy(str(body.get("by", JudgedBy.WORKER.value)))
        choices_raw = body.get("choices", {})
        if not isinstance(choices_raw, dict):
            raise GraphParseError("`judgement.choices` must be a map of choice name -> entry")
        return cls(
            prompt=body.text("prompt"),
            by=by,
            choices=[ChoiceDoc.of(name, entry) for name, entry in choices_raw.items()],
        )


@dataclass(frozen=True)
class RunStepDoc:
    """One command a hub command node executes, in authored order (#65).

    ``produces`` names a marker artifact recorded once the step exits 0 and skipped on
    a re-run — the at-least-once-per-step crash contract."""

    command: str
    name: str | None = None
    produces: str | None = None

    @classmethod
    def of(cls, raw: object) -> RunStepDoc:
        if isinstance(raw, str):
            return cls(command=raw)
        body = Parser.of(raw, "run entry")
        return cls(
            command=str(body.require("command", "a `run` entry must declare `command`")),
            name=body.text("name"),
            produces=body.text("produces"),
        )


@dataclass(frozen=True)
class ProducesSpec:
    """One ``produces:`` entry, kind-carrying (D1, issue #143).

    Authored either as a bare string (``kind`` defaults to :attr:`ArtifactKind.ASSET`)
    or as a mapping ``{name, kind}``; both forms normalize to this one type."""

    name: str
    kind: ArtifactKind = ArtifactKind.ASSET

    @classmethod
    def of(cls, raw: object, *, node: str) -> ProducesSpec:
        if isinstance(raw, str):
            return cls(name=raw, kind=ArtifactKind.ASSET)
        body = Parser.of(raw, f"node {node!r} `produces` entry")
        name = str(body.require("name", f"node {node!r}: a `produces` entry must declare `name`"))
        raw_kind = body.get("kind", ArtifactKind.ASSET.value)
        try:
            kind = ArtifactKind(str(raw_kind))
        except ValueError as exc:
            raise GraphParseError(
                f"node {node!r} produces `{name}`: unknown kind {raw_kind!r} — "
                f"expected `{ArtifactKind.ASSET.value}` or `{ArtifactKind.GIT_COMMIT.value}`"
            ) from exc
        return cls(name=name, kind=kind)


class NodeShape[StepT]:
    """What the authored and the reified node share — the one owner of the predicate
    keyed on that pair."""

    executor: Executor
    run: list[StepT]

    @property
    def is_hub_command_node(self) -> bool:
        """True for a generic hub command node — ``executor: hub`` plus a non-empty
        ``run:``. A predicate, not an assertion: an empty ``run:`` is authorable."""
        return self.executor is Executor.HUB and bool(self.run)


@dataclass(frozen=True)
class NodeDoc(NodeShape[RunStepDoc]):
    name: str
    executor: Executor
    prompt: str | None
    checks: list[str]
    produces: list[ProducesSpec]
    session: SessionMode
    retries_max: int | None
    retries_exhausted: str | None
    mode: str | None
    judgement: JudgementDoc | None
    # The kick-back cap (#64) — ``None`` accepts ``DEFAULT_BOUNCE_CAP``.
    bounce_cap: int | None = None
    # The hub command node's declared commands (#65, #67); empty on a worker node.
    run: list[RunStepDoc] = field(default_factory=list)
    # The pending-poll cadence (#66), in seconds — ``None`` accepts the executor default.
    poll_interval_seconds: int | None = None
    poll_timeout_seconds: int | None = None
    # See :class:`SessionRef` (issues #115, #144), whose fields these carry.
    session_source: str | None = None
    session_malformed: bool = False
    # Where ``checks:`` run (issue #114) — ``None`` runs them at the binding workdir's root.
    checks_cwd: str | None = None
    # The per-check timeout (issue #114), in seconds; a timeout is a red check.
    checks_timeout: int | None = None
    # Whether this node's completion may carry proposed work items — legal only on a
    # worker-judged runner node (D4); ``False`` is the default, off.
    proposes_work_items: bool = False

    @classmethod
    def of(cls, key: object, raw: object) -> NodeDoc:
        body = Parser.of(raw, key)
        name = str(key)
        executor = Executor(str(body.get("executor", Executor.RUNNER.value)))
        session = SessionRef.of(str(body.get("session", SessionMode.RESUME.value)))
        checks = [str(c) for c in body.items("checks")]
        produces = [ProducesSpec.of(p, node=name) for p in body.items("produces")]
        retries_max, retries_exhausted = cls._retries(body.get("retries"))
        bounce_cap = body.number("bounce_cap")
        poll_interval_seconds = body.number("poll_interval")
        poll_timeout_seconds = body.number("poll_timeout")
        checks_cwd = body.text("checks_cwd")
        checks_timeout = body.number("checks_timeout")
        run = [RunStepDoc.of(r) for r in body.items("run")]
        return cls(
            name=name,
            executor=executor,
            prompt=body.text("prompt"),
            checks=checks,
            produces=produces,
            session=session.mode,
            retries_max=retries_max,
            retries_exhausted=retries_exhausted,
            mode=body.text("mode"),
            bounce_cap=bounce_cap,
            judgement=JudgementDoc.of(body.get("judgement")),
            run=run,
            poll_interval_seconds=poll_interval_seconds,
            poll_timeout_seconds=poll_timeout_seconds,
            session_source=session.source,
            session_malformed=session.malformed,
            checks_cwd=checks_cwd,
            checks_timeout=checks_timeout,
            proposes_work_items=bool(body.get("proposes_work_items", False)),
        )

    @staticmethod
    def _retries(raw: object) -> tuple[int | None, str | None]:
        if not isinstance(raw, dict):
            return (None, None)
        raw_max = raw.get("max")
        raw_exhausted = raw.get("exhausted")
        return (
            int(raw_max) if raw_max is not None else None,
            str(raw_exhausted) if raw_exhausted is not None else None,
        )


@dataclass(frozen=True)
class RotatePolicy:
    """One declared session's rotation bounds (issue #144).

    Every threshold is optional; a head breaching *any* declared one is not resumed.
    ``max_invocations`` counts **harness invocations, not node-steps**."""

    max_context_tokens: int | None = None
    max_transcript_bytes: int | None = None
    max_invocations: int | None = None

    @classmethod
    def of(cls, raw: object, *, session: str) -> RotatePolicy:
        body = Parser.of(raw, f"session {session!r} `rotate`")
        return cls(
            max_context_tokens=body.number("max_context_tokens"),
            max_transcript_bytes=body.number("max_transcript_bytes"),
            max_invocations=body.number("max_invocations"),
        )


# Every key this hub's session parser reads by name (issue #351).
SESSION_KNOWN_KEYS = frozenset({"model", "effort", "rotate", "compaction_window"})


@dataclass(frozen=True)
class SessionDecl:
    """One graph-level named session declaration (issue #144).

    Carries workflow *policy* only (``bzh:app-agnostic-graphs``); ``model``, ``effort``,
    and ``compaction_window`` are all opaque to the hub."""

    name: str
    model: list[str] = field(default_factory=list)
    effort: str | None = None
    rotate: RotatePolicy | None = None
    compaction_window: str | None = None

    @classmethod
    def of(cls, key: object, raw: object) -> SessionDecl:
        body = Parser.of(raw, f"session {key!r}")
        name = str(key)
        unknown = sorted(set(body.body) - SESSION_KNOWN_KEYS)
        if unknown:
            # Pinned by tests/test_graph_sessions.py::test_an_unknown_session_key_is_rejected_naming_the_key
            raise GraphParseError(
                f"session {name!r}: unknown key(s) {', '.join(unknown)} — this hub "
                f"(blizzard {HUB_VERSION}) recognizes only {sorted(SESSION_KNOWN_KEYS)}"
            )
        raw_model = body.get("model")
        # A single string is the one-entry spelling, normalized to the same one-entry list
        # the sequence form parses to, so readers see exactly one shape.
        model = [str(raw_model)] if isinstance(raw_model, str) else [str(m) for m in body.items("model")]
        raw_rotate = body.get("rotate")
        return cls(
            name=name,
            model=model,
            effort=body.text("effort"),
            rotate=RotatePolicy.of(raw_rotate, session=name) if raw_rotate is not None else None,
            compaction_window=body.text("compaction_window"),
        )


@dataclass(frozen=True)
class GraphDoc:
    """A whole graph definition as authored — the validator's input."""

    name: str
    entry: str
    nodes: list[NodeDoc]
    # The graph-level named-session declarations (issue #144), keyed by name — a top-level
    # sibling of ``nodes:``, empty for every graph that declares none.
    sessions: dict[str, SessionDecl] = field(default_factory=dict)
    # The graph-scoped `artifacts:` map, name -> baked content — a top-level sibling of
    # `nodes:`/`sessions:`. Every value is already inlined text by the time this parses.
    artifacts: dict[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, raw: dict[str, object]) -> GraphDoc:
        """Parse a plain ``dict`` (from ``yaml.safe_load``) into a whole doc."""
        try:
            name = str(raw["name"])
            entry = str(raw["entry"])
            nodes_raw = raw["nodes"]
        except KeyError as exc:
            raise GraphParseError(f"graph definition missing required key: {exc}") from exc
        if not isinstance(nodes_raw, dict):
            raise GraphParseError("`nodes` must be a map of node name -> node")
        return cls(
            name=name,
            entry=entry,
            nodes=[NodeDoc.of(node_name, body) for node_name, body in nodes_raw.items()],
            sessions=cls._sessions(raw.get("sessions")),
            artifacts=cls._artifacts(raw.get("artifacts")),
        )

    @staticmethod
    def _sessions(raw: object) -> dict[str, SessionDecl]:
        if raw is None:
            return {}
        body = Parser.of(raw, "`sessions`").body
        return {str(name): SessionDecl.of(name, decl) for name, decl in body.items()}

    @staticmethod
    def _artifacts(raw: object) -> dict[str, str]:
        if raw is None:
            return {}
        body = Parser.of(raw, "`artifacts`").body
        artifacts: dict[str, str] = {}
        for name, content in body.items():
            # Text, never coerced: a valueless `docket:` or a nested block is a malformation,
            # not an artifact whose content is that value's repr.
            if not isinstance(content, str):
                where = f"`artifacts.{name}`"
                raise GraphParseError(f"expected text at {where!r}, got {type(content).__name__}")
            artifacts[str(name)] = content
        return artifacts

    def node(self, name: str) -> NodeDoc | None:
        return next((n for n in self.nodes if n.name == name), None)


# --- Reified graph (post-mint, id-carrying, immutable) ----------------------


@dataclass(frozen=True)
class Choice:
    """One selectable outcome of one node's judgement."""

    choice_id: str
    name: str
    description: str
    # Gated on green checks (issue #114) — see ``ChoiceDoc.requires_checks``.
    requires_checks: bool = False


@dataclass(frozen=True)
class Edge:
    """A directed, choice-keyed connection out of one node.

    ``target_graph`` is ``to_node_name``'s parsed name when the edge is cross-graph
    (issue #90); ``model`` overrides the chunk's model when the migration re-pins it."""

    from_node_id: str
    choice_id: str
    to_node_name: str  # a node name of this graph, RESERVED_TERMINAL, or ``graph:<name>``
    prompt_addendum: str | None = None
    target_graph: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class RunStep:
    """One reified command a hub command node executes, in authored order (#65)."""

    command: str
    name: str | None = None
    produces: str | None = None


@dataclass(frozen=True)
class Node(NodeShape[RunStep]):
    """One station in one immutable graph."""

    node_id: str
    graph_id: str
    name: str
    executor: Executor
    prompt: str | None
    checks: list[str]
    produces: list[ProducesSpec]
    session: SessionMode
    judged_by: JudgedBy
    retries_max: int | None
    retries_exhausted: str | None
    mode: str | None
    judgement_prompt: str | None = None
    choices: list[Choice] = field(default_factory=list)
    # The kick-back cap (#64) — ``None`` accepts ``DEFAULT_BOUNCE_CAP``.
    bounce_cap: int | None = None
    # The generic hub command node's declared commands (#65) — see ``NodeDoc.run``.
    run: list[RunStep] = field(default_factory=list)
    # The pending-poll cadence (#66), in seconds — see ``NodeDoc.poll_interval_seconds``.
    poll_interval_seconds: int | None = None
    poll_timeout_seconds: int | None = None
    # A validated graph never carries a malformed session, so no malformed flag here.
    session_source: str | None = None
    # See ``NodeDoc.checks_cwd`` / ``NodeDoc.checks_timeout`` (issue #114).
    checks_cwd: str | None = None
    checks_timeout: int | None = None
    # See ``NodeDoc.proposes_work_items``.
    proposes_work_items: bool = False


@dataclass(frozen=True)
class GraphArtifact:
    """One graph-scoped artifact baked into the mint — its authored name, the
    loader-inlined content, and its authored-order position."""

    name: str
    content: str
    ordinal: int


@dataclass(frozen=True)
class Graph:
    graph_id: str
    name: str
    entry_node_id: str
    nodes: list[Node]
    edges: list[Edge]
    created_at: datetime
    # The graph-level named-session declarations (issue #144), in authored order.
    sessions: list[SessionDecl] = field(default_factory=list)
    # The graph-scoped `artifacts:` declarations, in authored order.
    artifacts: list[GraphArtifact] = field(default_factory=list)

    @property
    def declares_git_commit(self) -> bool:
        """Whether any node declares a ``git_commit``-kind ``produces:`` — the graph's own
        statement of intent, and the only thing that tells an empty delivery set apart
        from a failed one."""
        return any(spec.kind is ArtifactKind.GIT_COMMIT for node in self.nodes for spec in node.produces)

    def session_by_name(self, name: str) -> SessionDecl | None:
        return next((s for s in self.sessions if s.name == name), None)

    def node_by_name(self, name: str) -> Node | None:
        return next((n for n in self.nodes if n.name == name), None)

    def node_by_id(self, node_id: str) -> Node | None:
        return next((n for n in self.nodes if n.node_id == node_id), None)

    def edges_from(self, node_id: str) -> list[Edge]:
        return [e for e in self.edges if e.from_node_id == node_id]

    def edge_for_choice(self, node_id: str, choice_name: str) -> Edge | None:
        """The edge a node's judgement choice keys, matched by choice *name*."""
        choice_ids = {
            c.choice_id for n in self.nodes if n.node_id == node_id for c in n.choices if c.name == choice_name
        }
        return next((e for e in self.edges if e.from_node_id == node_id and e.choice_id in choice_ids), None)


# --- Mint selection (which mint of a name a chunk sees) ---------------------


@dataclass(frozen=True)
class Mint:
    """One minted graph, ordered by when it was minted."""

    graph: Graph

    @classmethod
    def of(cls, graph: Graph) -> Mint:
        return cls(graph)

    @property
    def order(self) -> tuple[datetime, str]:
        # Tie-break on graph_id descending — ULIDs sort lexically by creation.
        return (self.graph.created_at, self.graph.graph_id)

    def newer_than(self, other: Mint) -> bool:
        """**Strictly** newer — pinned by
        tests/test_follow_latest_policy.py::test_the_policy_never_drags_a_chunk_backwards_onto_an_older_mint"""
        return self.order > other.order


@dataclass(frozen=True)
class Mints:
    """Every mint of every name, with the retired ones out of contention (issue #101)."""

    mints: list[Mint]
    retired_ids: Collection[str]

    @classmethod
    def of(cls, graphs: list[Graph], *, retired_ids: Collection[str]) -> Mints:
        return cls([Mint.of(g) for g in graphs], retired_ids)

    @property
    def newest_by_name(self) -> dict[str, Mint]:
        newest: dict[str, Mint] = {}
        for candidate in self.mints:
            if candidate.graph.graph_id in self.retired_ids:
                continue
            current = newest.get(candidate.graph.name)
            if current is None or candidate.newer_than(current):
                newest[candidate.graph.name] = candidate
        return newest

    @property
    def effective(self) -> dict[str, bool]:
        """Whether each ``graph_id`` is the newest non-retired mint of its name.

        ``retired_ids`` carries no default, so omitting it raises — pinned by
        tests/test_graph_domain.py::test_mints_effective_requires_retired_ids_explicitly"""
        effective_ids = {m.graph.graph_id for m in self.newest_by_name.values()}
        return {m.graph.graph_id: m.graph.graph_id in effective_ids for m in self.mints}


@dataclass(frozen=True)
class FollowLatest:
    """Whether a chunk pinned to a graph follows the newest mint of its name (issue #164).

    ``hub_default`` carries no default, so omitting it raises — pinned by
    tests/test_pin_hub_domain.py::test_follow_latest_requires_hub_default_explicitly"""

    enabled: bool

    @classmethod
    def of(cls, graph_policy: bool | None, *, hub_default: bool) -> FollowLatest:
        return cls(hub_default if graph_policy is None else graph_policy)


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


class IReadGraphRepository(Protocol):
    """Read-only graph access. Controllers at the edges depend on this variant."""

    def get(self, graph_id: str) -> Graph | None: ...
    def get_enabled_by_name(self, name: str) -> Graph | None:
        """The newest non-retired graph with ``name`` — the default-graph pin lookup.

        Excludes every retired ``graph_id`` (issue #101), so a name whose every mint is
        retired resolves to ``None``."""
        ...

    def list_all(self) -> list[Graph]: ...

    def newest_definition_yaml(self, name: str) -> str | None:
        """The newest-minted graph of ``name``'s source YAML, ``None`` if never minted.

        Newest-*minted*, not newest-enabled: retirement does not change what was minted,
        so it is not consulted here (issue #146).
        """
        ...

    def is_retired(self, graph_id: str) -> bool:
        """Whether ``graph_id``'s newest lifecycle fact reads retired (issue #101).

        ``False`` for a ``graph_id`` with no lifecycle fact at all — every freshly
        minted graph starts enabled.
        """
        ...

    def retired_graph_ids(self) -> set[str]:
        """Every ``graph_id`` whose newest lifecycle fact reads retired (issue #101).

        The set :class:`Mints` excludes from candidacy — the bulk counterpart to
        :meth:`is_retired`.
        """
        ...

    def follow_latest(self, graph_id: str) -> bool | None:
        """This graph's own follow-latest policy — the stored tri-state (issue #164).

        ``None`` — the value for a graph with no policy fact — inherits the hub-level
        setting (:class:`FollowLatest`). Newest-fact-wins.
        """
        ...


class IWriteGraphRepository(IReadGraphRepository, Protocol):
    """Read-write graph access. Only the domain layer depends on this variant."""

    def mint(self, graph: Graph, *, definition_yaml: str, at: datetime) -> None:
        """Persist a reified, immutable graph and its source YAML."""
        ...

    def record_lifecycle(self, graph_id: str, *, retired: bool, at: datetime, by: str) -> None:
        """Append a ``graph.retired``/``graph.enabled`` fact — newest-fact-wins (issue #101).

        Never touches the ``graphs`` row itself — it stays insert-only and immutable.
        """
        ...

    def record_policy(self, graph_id: str, *, follow_latest: bool | None, at: datetime, by: str) -> None:
        """Append a follow-latest policy fact — newest-fact-wins (issue #164).

        ``follow_latest=None`` is a real, recordable value — "revert to inheriting",
        not "leave unchanged". Never touches the ``graphs`` row.
        """
        ...
