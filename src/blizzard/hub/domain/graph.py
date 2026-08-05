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

from blizzard.hub.domain.artifacts import ArtifactKind

# The reserved terminal a choice may point at instead of a node name.
RESERVED_TERMINAL = "done"

# The cross-graph target prefix (issue #90): a ``to: graph:<name>`` choice re-pins the
# chunk rather than transitioning it (``bzh:migration-not-transition``).
GRAPH_TARGET_PREFIX = "graph:"


def classify_choice_target(to: str) -> tuple[str, str | None]:
    """Classify a choice ``to:`` value into ``(kind, value)`` — pure syntax (issue #90).

    ``("node", <name>)`` a same-graph node or the reserved terminal; ``("graph", <name>)``
    a well-formed ``graph:<name>``; ``("malformed", None)`` any other ``graph:``-prefixed
    value. Parsing never validates."""
    if not to.startswith(GRAPH_TARGET_PREFIX):
        return ("node", to)
    name = to[len(GRAPH_TARGET_PREFIX) :]
    if not name or ":" in name:
        return ("malformed", None)
    return ("graph", name)


def target_graph_of(to_node_name: str) -> str | None:
    """The target graph name a reified edge's ``to_node_name`` encodes, or ``None`` for
    a same-graph node or terminal target (issue #90).

    A cross-graph edge persists its target as the raw ``graph:<name>`` string, with no
    separate column; this re-derives the structured name on load."""
    kind, value = classify_choice_target(to_node_name)
    return value if kind == "graph" else None


class Executor(StrEnum):
    """Where a node's step runs."""

    RUNNER = "runner"
    HUB = "hub"


class JudgedBy(StrEnum):
    """Who renders a node's exit judgement — the structural gate marker."""

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


def classify_session(raw: str) -> tuple[SessionMode, str | None, bool]:
    """Classify an authored ``session:`` value into ``(mode, source, malformed)`` — pure
    syntax, mirroring :func:`classify_choice_target` (issues #115, #144).

    ``source`` is the ``<name>`` of a ``resume:``/``fresh:`` form, carried verbatim and
    ``None`` for a bare form; under ``malformed`` neither may be relied on."""
    if raw == SessionMode.FRESH.value:
        return (SessionMode.FRESH, None, False)
    if raw == SessionMode.RESUME.value:
        return (SessionMode.RESUME, None, False)
    if raw.startswith(SESSION_RESUME_TARGET_PREFIX):
        name = raw[len(SESSION_RESUME_TARGET_PREFIX) :]
        if name:
            return (SessionMode.RESUME, name, False)
    if raw.startswith(SESSION_FRESH_TARGET_PREFIX):
        name = raw[len(SESSION_FRESH_TARGET_PREFIX) :]
        if name:
            return (SessionMode.FRESH, name, False)
    return (SessionMode.RESUME, None, True)


class RetriesExhausted(StrEnum):
    """The only exhaustion target in the MVP."""

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


@dataclass(frozen=True)
class JudgementDoc:
    """A node's judgement block as authored."""

    prompt: str | None
    by: JudgedBy
    choices: list[ChoiceDoc]


@dataclass(frozen=True)
class RunStepDoc:
    """One command a hub command node executes, in authored order (#65).

    ``produces`` names a marker artifact recorded once the step exits 0 and skipped on
    a re-run — the at-least-once-per-step crash contract."""

    command: str
    name: str | None = None
    produces: str | None = None


@dataclass(frozen=True)
class ProducesSpec:
    """One ``produces:`` entry, kind-carrying (D1, issue #143).

    Authored either as a bare string (``kind`` defaults to :attr:`ArtifactKind.ASSET`)
    or as a mapping ``{name, kind}``; both forms normalize to this one type."""

    name: str
    kind: ArtifactKind = ArtifactKind.ASSET


@dataclass(frozen=True)
class NodeDoc:
    """One node as authored."""

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
    # The parsed ``<name>`` of a ``resume:``/``fresh:`` form (issues #115, #144), read
    # with ``session`` beside it; ``None`` for a bare form.
    session_source: str | None = None
    # Whether the authored ``session:`` value was structurally malformed — kept separate
    # from ``session_source``, which is ``None`` in that case too.
    session_malformed: bool = False
    # Where ``checks:`` run (issue #114) — relative to the leased env's binding workdir;
    # ``None`` runs them at its root. Per-check cwd is a documented deferral.
    checks_cwd: str | None = None
    # The per-check timeout (issue #114), in seconds; a timeout is a red check.
    checks_timeout: int | None = None


@dataclass(frozen=True)
class RotatePolicy:
    """One declared session's rotation bounds (issue #144).

    Every threshold is optional; a head breaching *any* declared one is not resumed.
    ``max_invocations`` counts **harness invocations, not node-steps**."""

    max_context_tokens: int | None = None
    max_transcript_bytes: int | None = None
    max_invocations: int | None = None


@dataclass(frozen=True)
class SessionDecl:
    """One graph-level named session declaration (issue #144).

    Carries workflow *policy* only, never application knowledge
    (``bzh:app-agnostic-graphs``); ``model`` and ``effort`` are opaque to the hub."""

    name: str
    model: list[str] = field(default_factory=list)
    effort: str | None = None
    rotate: RotatePolicy | None = None


@dataclass(frozen=True)
class GraphDoc:
    """A whole graph definition as authored — the validator's input."""

    name: str
    entry: str
    nodes: list[NodeDoc]
    # The graph-level named-session declarations (issue #144), keyed by name — a top-level
    # sibling of ``nodes:``, empty for every graph that declares none.
    sessions: dict[str, SessionDecl] = field(default_factory=dict)

    def node(self, name: str) -> NodeDoc | None:
        return next((n for n in self.nodes if n.name == name), None)


def parse_graph_doc(raw: dict[str, object]) -> GraphDoc:
    """Parse a plain ``dict`` (from ``yaml.safe_load``) into a :class:`GraphDoc`.

    Structural coercion only — never validation. A malformed shape raises
    :class:`GraphParseError`; whether a well-formed doc is *legal* is the validator's."""
    try:
        name = str(raw["name"])
        entry = str(raw["entry"])
        nodes_raw = raw["nodes"]
    except KeyError as exc:
        raise GraphParseError(f"graph definition missing required key: {exc}") from exc
    if not isinstance(nodes_raw, dict):
        raise GraphParseError("`nodes` must be a map of node name -> node")

    nodes = [_parse_node(str(node_name), _as_dict(body, node_name)) for node_name, body in nodes_raw.items()]
    return GraphDoc(name=name, entry=entry, nodes=nodes, sessions=_parse_sessions(raw.get("sessions")))


def _parse_sessions(raw: object) -> dict[str, SessionDecl]:
    """Parse the optional top-level ``sessions:`` map (issue #144).

    Absent reads as ``{}``; structural coercion only, never validation."""
    if raw is None:
        return {}
    body = _as_dict(raw, "`sessions`")
    return {str(name): _parse_session(str(name), _as_dict(decl, f"session {name!r}")) for name, decl in body.items()}


def _parse_session(name: str, body: dict[str, object]) -> SessionDecl:
    raw_model = body.get("model")
    # A single string is the one-entry spelling, normalized to the same one-entry list
    # the sequence form parses to, so readers see exactly one shape.
    model = [str(raw_model)] if isinstance(raw_model, str) else [str(m) for m in _as_list(raw_model)]
    raw_effort = body.get("effort")
    raw_rotate = body.get("rotate")
    rotate = _parse_rotate(_as_dict(raw_rotate, f"session {name!r} `rotate`")) if raw_rotate is not None else None
    return SessionDecl(
        name=name,
        model=model,
        effort=str(raw_effort) if raw_effort is not None else None,
        rotate=rotate,
    )


def _parse_rotate(body: dict[str, object]) -> RotatePolicy:
    def _int_or_none(key: str) -> int | None:
        value = body.get(key)
        return int(str(value)) if value is not None else None

    return RotatePolicy(
        max_context_tokens=_int_or_none("max_context_tokens"),
        max_transcript_bytes=_int_or_none("max_transcript_bytes"),
        max_invocations=_int_or_none("max_invocations"),
    )


def _parse_node(name: str, body: dict[str, object]) -> NodeDoc:
    executor = Executor(str(body.get("executor", Executor.RUNNER.value)))
    session, session_source, session_malformed = classify_session(str(body.get("session", SessionMode.RESUME.value)))
    checks = [str(c) for c in _as_list(body.get("checks", []))]
    produces = [_parse_produces_entry(p, name) for p in _as_list(body.get("produces", []))]
    retries = body.get("retries")
    retries_max: int | None = None
    retries_exhausted: str | None = None
    if isinstance(retries, dict):
        raw_max = retries.get("max")
        retries_max = int(raw_max) if raw_max is not None else None
        raw_exhausted = retries.get("exhausted")
        retries_exhausted = str(raw_exhausted) if raw_exhausted is not None else None
    prompt = body.get("prompt")
    mode = body.get("mode")
    raw_bounce_cap = body.get("bounce_cap")
    bounce_cap = int(str(raw_bounce_cap)) if raw_bounce_cap is not None else None
    raw_poll_interval = body.get("poll_interval")
    poll_interval_seconds = int(str(raw_poll_interval)) if raw_poll_interval is not None else None
    raw_poll_timeout = body.get("poll_timeout")
    poll_timeout_seconds = int(str(raw_poll_timeout)) if raw_poll_timeout is not None else None
    raw_checks_cwd = body.get("checks_cwd")
    checks_cwd = str(raw_checks_cwd) if raw_checks_cwd is not None else None
    raw_checks_timeout = body.get("checks_timeout")
    checks_timeout = int(str(raw_checks_timeout)) if raw_checks_timeout is not None else None
    run = [_parse_run_step(r) for r in _as_list(body.get("run", []))]
    return NodeDoc(
        name=name,
        executor=executor,
        prompt=str(prompt) if prompt is not None else None,
        checks=checks,
        produces=produces,
        session=session,
        retries_max=retries_max,
        retries_exhausted=retries_exhausted,
        mode=str(mode) if mode is not None else None,
        bounce_cap=bounce_cap,
        judgement=_parse_judgement(body.get("judgement")),
        run=run,
        poll_interval_seconds=poll_interval_seconds,
        poll_timeout_seconds=poll_timeout_seconds,
        session_source=session_source,
        session_malformed=session_malformed,
        checks_cwd=checks_cwd,
        checks_timeout=checks_timeout,
    )


def _parse_produces_entry(raw: object, node_name: str) -> ProducesSpec:
    """Normalize one authored ``produces:`` entry (D1, issue #143).

    A bare string names an asset; a mapping ``{name, kind}`` names an explicit kind.
    Structural coercion only — an unrecognized ``kind`` raises :class:`GraphParseError`
    rather than a bare :class:`ValueError`, since these entries are user-authored."""
    if isinstance(raw, str):
        return ProducesSpec(name=raw, kind=ArtifactKind.ASSET)
    body = _as_dict(raw, f"node {node_name!r} `produces` entry")
    try:
        entry_name = str(body["name"])
    except KeyError as exc:
        raise GraphParseError(f"node {node_name!r}: a `produces` entry must declare `name`") from exc
    raw_kind = body.get("kind", ArtifactKind.ASSET.value)
    try:
        kind = ArtifactKind(str(raw_kind))
    except ValueError as exc:
        raise GraphParseError(
            f"node {node_name!r} produces `{entry_name}`: unknown kind {raw_kind!r} — "
            f"expected `{ArtifactKind.ASSET.value}` or `{ArtifactKind.GIT_COMMIT.value}`"
        ) from exc
    return ProducesSpec(name=entry_name, kind=kind)


def _parse_run_step(raw: object) -> RunStepDoc:
    if isinstance(raw, str):
        return RunStepDoc(command=raw)
    body = _as_dict(raw, "run entry")
    try:
        command = str(body["command"])
    except KeyError as exc:
        raise GraphParseError("a `run` entry must declare `command`") from exc
    name = body.get("name")
    produces = body.get("produces")
    return RunStepDoc(
        command=command,
        name=str(name) if name is not None else None,
        produces=str(produces) if produces is not None else None,
    )


def _parse_judgement(raw: object) -> JudgementDoc | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise GraphParseError("`judgement` must be a map")
    by = JudgedBy(str(raw.get("by", JudgedBy.WORKER.value)))
    prompt = raw.get("prompt")
    choices_raw = raw.get("choices", {})
    if not isinstance(choices_raw, dict):
        raise GraphParseError("`judgement.choices` must be a map of choice name -> entry")
    choices = [_parse_choice(str(cn), _as_dict(cb, cn)) for cn, cb in choices_raw.items()]
    return JudgementDoc(prompt=str(prompt) if prompt is not None else None, by=by, choices=choices)


def _parse_choice(name: str, body: dict[str, object]) -> ChoiceDoc:
    description = body.get("description")
    to = body.get("to")
    addendum = body.get("prompt_addendum")
    model = body.get("model")
    requires_checks = bool(body.get("requires_checks", False))
    to_str = str(to) if to is not None else None
    # Structural coercion only — a malformed ``graph:`` form parses to ``target_graph=None``
    # and the validator rejects it against the raw ``to`` (parse never validates).
    target_graph = None
    if to_str is not None:
        kind, value = classify_choice_target(to_str)
        if kind == "graph":
            target_graph = value
    return ChoiceDoc(
        name=name,
        description=str(description) if description is not None else None,
        to=to_str,
        prompt_addendum=str(addendum) if addendum is not None else None,
        target_graph=target_graph,
        model=str(model) if model is not None else None,
        requires_checks=requires_checks,
    )


def _as_dict(value: object, where: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise GraphParseError(f"expected a map at {where!r}, got {type(value).__name__}")
    return value


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise GraphParseError(f"expected a list, got {type(value).__name__}")
    return value


class GraphParseError(ValueError):
    """A graph definition is structurally malformed (before validation)."""


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
class Node:
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
    # The session reference target (issues #115, #144) — see ``NodeDoc.session_source``.
    # A validated graph never carries a malformed session, so no malformed flag here.
    session_source: str | None = None
    # See ``NodeDoc.checks_cwd`` / ``NodeDoc.checks_timeout`` (issue #114).
    checks_cwd: str | None = None
    checks_timeout: int | None = None

    @property
    def is_hub_command_node(self) -> bool:
        """True for a generic hub command node — ``executor: hub`` plus a non-empty
        ``run:``. A predicate, not an assertion: an empty ``run:`` is authorable."""
        return self.executor is Executor.HUB and bool(self.run)


@dataclass(frozen=True)
class Graph:
    """A reified, immutable workflow graph."""

    graph_id: str
    name: str
    entry_node_id: str
    nodes: list[Node]
    edges: list[Edge]
    created_at: datetime
    # The graph-level named-session declarations (issue #144), in authored order;
    # :meth:`session_by_name` is the lookup.
    sessions: list[SessionDecl] = field(default_factory=list)

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


def mark_effective(graphs: list[Graph], *, retired_ids: Collection[str]) -> dict[str, bool]:
    """Mark the newest non-retired ``created_at`` graph per ``name`` as effective.

    Keyed by ``graph_id``. A retired graph is never a candidate. ``retired_ids`` carries
    no default, so omitting it raises — pinned by
    tests/test_graph_domain.py::test_mark_effective_requires_retired_ids_explicitly"""
    newest_by_name: dict[str, Graph] = {}
    for graph in graphs:
        if graph.graph_id in retired_ids:
            continue
        current = newest_by_name.get(graph.name)
        # Tie-break on graph_id descending — ULIDs sort lexically by creation.
        if current is None or (graph.created_at, graph.graph_id) > (current.created_at, current.graph_id):
            newest_by_name[graph.name] = graph
    effective_ids = {g.graph_id for g in newest_by_name.values()}
    return {g.graph_id: g.graph_id in effective_ids for g in graphs}


def is_newer_mint(candidate: Graph, current: Graph) -> bool:
    """Whether ``candidate`` is a strictly newer mint than ``current``.

    ``created_at`` first, ``graph_id`` as the tie-break. **Strictly** newer — pinned by
    tests/test_follow_latest_policy.py::test_the_policy_never_drags_a_chunk_backwards_onto_an_older_mint"""
    return (candidate.created_at, candidate.graph_id) > (current.created_at, current.graph_id)


def resolve_follow_latest(graph_policy: bool | None, *, hub_default: bool) -> bool:
    """Whether a chunk pinned to a graph follows the newest mint of its name (issue #164).

    The graph's own tri-state wins where set; ``None`` inherits ``hub_default``, which
    carries no default, so omitting it raises — pinned by
    tests/test_pin_hub_domain.py::test_resolve_follow_latest_requires_hub_default_explicitly"""
    return hub_default if graph_policy is None else graph_policy


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

        The set :func:`mark_effective` excludes from candidacy — the bulk counterpart
        to :meth:`is_retired`.
        """
        ...

    def follow_latest(self, graph_id: str) -> bool | None:
        """This graph's own follow-latest policy — the stored tri-state (issue #164).

        ``None`` — the value for a graph with no policy fact — inherits the hub-level
        setting (:func:`resolve_follow_latest`). Newest-fact-wins.
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
