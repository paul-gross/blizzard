"""Workflow-graph domain model — the definition chunks travel.

Two representations live here, and the split is deliberate:

* the **authoring doc** (:class:`GraphDoc` and friends) — the plain-data shape a
  ``POST /graphs`` YAML body parses into, before any ids are minted. It is what
  the mint-time validator (:mod:`blizzard.hub.domain.graph_validation`) checks.
* the **reified graph** (:class:`Graph`, :class:`Node`, :class:`Choice`,
  :class:`Edge`) — the immutable, id-carrying entities a validated doc compiles
  into at mint, and what the hub store persists.

Everything here is a dependency-free domain type (``bzh:domain-core``): no YAML, no
SQLAlchemy, no FastAPI. Parsing YAML text into a ``dict`` and inlining prompt
*file* references are edge concerns done before :func:`parse_graph_doc` and the
validator run — the domain sees only already-loaded data.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

# The reserved terminal a choice may point at instead of a node name.
RESERVED_TERMINAL = "done"

# The reserved cross-graph target prefix (issue #90). A judgement choice whose ``to:``
# is ``graph:<name>`` targets **another graph** — taking it re-pins the chunk to that
# graph and re-queues it (a migration, not a same-graph transition). Graph-only: the
# landing node is resolved at apply time by name-match-else-entry against the target
# graph (``bzh:migration-not-transition``); an explicit ``graph:<name>:<node>`` landing
# override is deferred (issue #90 out-of-scope).
GRAPH_TARGET_PREFIX = "graph:"


def classify_choice_target(to: str) -> tuple[str, str | None]:
    """Classify a choice ``to:`` value into ``(kind, value)`` — a pure syntax parser (issue #90).

    - ``("node", <name>)`` — a same-graph node name, or the reserved terminal ``done``.
    - ``("graph", <name>)`` — a well-formed cross-graph target ``graph:<name>``.
    - ``("malformed", None)`` — a ``graph:``-prefixed value that is not ``graph:<name>``
      (empty name, or an extra ``:`` — the deferred explicit-node override).

    Kept a pure function so both the mint-time validator (which rejects ``malformed``)
    and the store's edge hydration (which re-derives the target from the persisted raw
    ``to_node_name``) classify identically.
    """
    if not to.startswith(GRAPH_TARGET_PREFIX):
        return ("node", to)
    name = to[len(GRAPH_TARGET_PREFIX) :]
    if not name or ":" in name:
        return ("malformed", None)
    return ("graph", name)


def target_graph_of(to_node_name: str) -> str | None:
    """The cross-graph target graph name a reified edge's ``to_node_name`` encodes, or
    ``None`` for a same-graph node / terminal target (issue #90).

    A cross-graph edge persists its target as the raw ``graph:<name>`` string in
    ``to_node_name`` (no separate column) — this re-derives the structured name on load.
    A malformed form never reaches here: the validator rejects it before mint.
    """
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


# The prefix for a node-entry targeted resume (issue #115): ``session: resume:<node>``
# resumes node ``<node>``'s most-recent session instead of the chunk's most-recent
# session overall (bare ``resume``).
SESSION_RESUME_TARGET_PREFIX = "resume:"


def classify_session(raw: str) -> tuple[SessionMode, str | None, bool]:
    """Classify a node's authored ``session:`` value into ``(mode, source, malformed)``
    (issue #115) — a pure syntax parser, mirroring :func:`classify_choice_target`.

    - ``"resume"`` -> ``(RESUME, None, False)`` — resume the chunk's most-recent
      session (any node).
    - ``"resume:<name>"`` -> ``(RESUME, "<name>", False)`` — resume node ``<name>``'s
      most-recent session. ``<name>`` is carried verbatim; whether it names an
      existing node is the validator's job — structural parse only, exactly the
      parse-never-validates split :func:`classify_choice_target` already keeps.
    - ``"fresh"`` -> ``(FRESH, None, False)``.
    - anything else (``"resume:"`` with an empty name, ``"fresh:x"``, or an
      unrecognized token) -> ``malformed=True``; ``mode``/``source`` are placeholders
      a caller must not rely on.
    """
    if raw == SessionMode.FRESH.value:
        return (SessionMode.FRESH, None, False)
    if raw == SessionMode.RESUME.value:
        return (SessionMode.RESUME, None, False)
    if raw.startswith(SESSION_RESUME_TARGET_PREFIX):
        name = raw[len(SESSION_RESUME_TARGET_PREFIX) :]
        if name:
            return (SessionMode.RESUME, name, False)
    return (SessionMode.RESUME, None, True)


class RetriesExhausted(StrEnum):
    """The only exhaustion target in the MVP."""

    ESCALATE = "escalate"


# The reserved default outcome names a hub command node's machinery maps a command's
# exit code to when the command prints no explicit choice (#65): exit 0 -> success,
# nonzero -> failure. A node authors a matching choice to route either default
# anywhere it likes, including straight to the reserved terminal — no node name is
# privileged by the engine (#67); a command may also print one of the node's other
# authored choice names (e.g. ``landed``/``conflict``) on its last stdout line to
# select it directly.
HUB_DEFAULT_SUCCESS_CHOICE = "success"
HUB_DEFAULT_FAILURE_CHOICE = "failure"

# The reserved **pending** outcome (#66) — a hub command node's ``run:`` step signals
# it by printing this literal name on its last stdout line (exit code 0; a nonzero
# exit is always a failure, never pending). Recognized regardless of whether the node
# authors a matching choice — like ``success``/``failure``, it is machinery-reserved,
# not an authored edge: the executor intercepts it *before* any edge lookup, records a
# poll-attempt fact, releases the fleet-wide slot, and re-runs the node's ``run:`` list
# (skipping any step whose ``produces:`` marker already exists) once ``poll_interval``
# has elapsed — never routing a transition while pending persists.
HUB_PENDING_CHOICE = "pending"

# The fleet-wide default kick-back cap (#64) — a hub node whose author omits
# ``bounce_cap`` tolerates this many bounces (conflict/CI-red/master-moved kick-backs)
# before the chunk escalates. Per-node, not global: a flaky-CI node can set its own,
# stricter or looser cap by authoring the field.
DEFAULT_BOUNCE_CAP = 5


# --- Authoring doc (parsed from a YAML body, pre-mint) ----------------------


@dataclass(frozen=True)
class ChoiceDoc:
    """One fused choice/edge entry as authored.

    ``to`` is the raw authored target — a same-graph node name, the reserved terminal,
    or a cross-graph ``graph:<name>`` (issue #90). ``target_graph`` is the parsed graph
    name when ``to`` is a well-formed cross-graph form (``None`` otherwise); a malformed
    ``graph:`` form leaves it ``None`` and is rejected by the validator, which reads the
    raw ``to``. ``model`` is an optional per-choice model override applied when the choice
    migrates the chunk to another graph (``None`` keeps the chunk's current model)."""

    name: str
    description: str | None
    to: str | None
    prompt_addendum: str | None = None
    target_graph: str | None = None
    model: str | None = None


@dataclass(frozen=True)
class JudgementDoc:
    """A node's judgement block as authored."""

    prompt: str | None
    by: JudgedBy
    choices: list[ChoiceDoc]


@dataclass(frozen=True)
class RunStepDoc:
    """One command a hub command node executes, in authored order (#65).

    ``produces``, when set, names a marker artifact: the engine records it once this
    step exits 0, and SKIPS the step on any later re-run once it already exists — the
    at-least-once-per-step crash contract, and the redelivery reconciliation
    generalized (``record_delivery_repo_landed``'s per-repo skip is the pattern this
    generalizes). ``name`` is a human label only (surfaced in logs/artifacts); it
    defaults to the step's 1-based position when omitted.
    """

    command: str
    name: str | None = None
    produces: str | None = None


@dataclass(frozen=True)
class NodeDoc:
    """One node as authored."""

    name: str
    executor: Executor
    prompt: str | None
    checks: list[str]
    produces: list[str]
    session: SessionMode
    retries_max: int | None
    retries_exhausted: str | None
    mode: str | None
    judgement: JudgementDoc | None
    # The kick-back cap (#64) — ``None`` accepts the fleet default (``DEFAULT_BOUNCE_CAP``);
    # a hub node may author its own, stricter or looser.
    bounce_cap: int | None = None
    # The generic hub command node's declared commands (#65) — non-empty exactly on a
    # node ``executor: hub`` authors as the generic primitive; empty on every worker
    # node. Every hub node authors ``run:`` since #67 retired the deliver special case.
    run: list[RunStepDoc] = field(default_factory=list)
    # The pending-poll cadence (#66), in seconds — ``None`` accepts the executor's
    # own default (:data:`blizzard.hub.delivery.hub_node.DEFAULT_POLL_INTERVAL` /
    # ``DEFAULT_POLL_TIMEOUT``). Legal only on a generic hub command node
    # (``executor: hub`` with ``run:``) — a node with no ``pending``-reporting step
    # never reads either.
    poll_interval_seconds: int | None = None
    poll_timeout_seconds: int | None = None
    # The targeted-resume source node name (issue #115) — the parsed ``<name>`` of a
    # ``session: resume:<name>`` form, ``None`` for bare ``resume``/``fresh``. Set by
    # :func:`classify_session`; whether it names an existing node is the validator's job.
    session_source: str | None = None
    # Whether the authored ``session:`` value was structurally malformed (issue #115) —
    # e.g. ``resume:`` with an empty name, ``fresh:x``, or an unrecognized token. Kept
    # separate from ``session_source`` (which is ``None`` in this case too) so the
    # validator can distinguish "malformed syntax" from "well-formed but names no node"
    # without re-parsing raw YAML (parse never validates, but the validator still needs
    # the parse's own verdict carried forward — ``bzh:one-owner``).
    session_malformed: bool = False


@dataclass(frozen=True)
class GraphDoc:
    """A whole graph definition as authored — the validator's input."""

    name: str
    entry: str
    nodes: list[NodeDoc]

    def node(self, name: str) -> NodeDoc | None:
        return next((n for n in self.nodes if n.name == name), None)


def parse_graph_doc(raw: dict[str, object]) -> GraphDoc:
    """Parse a plain ``dict`` (from ``yaml.safe_load``) into a :class:`GraphDoc`.

    Structural coercion only — never validation. A malformed shape raises
    :class:`GraphParseError`; whether a well-formed doc is *legal* is the
    validator's job (:mod:`blizzard.hub.domain.graph_validation`).
    """
    try:
        name = str(raw["name"])
        entry = str(raw["entry"])
        nodes_raw = raw["nodes"]
    except KeyError as exc:
        raise GraphParseError(f"graph definition missing required key: {exc}") from exc
    if not isinstance(nodes_raw, dict):
        raise GraphParseError("`nodes` must be a map of node name -> node")

    nodes = [_parse_node(str(node_name), _as_dict(body, node_name)) for node_name, body in nodes_raw.items()]
    return GraphDoc(name=name, entry=entry, nodes=nodes)


def _parse_node(name: str, body: dict[str, object]) -> NodeDoc:
    executor = Executor(str(body.get("executor", Executor.RUNNER.value)))
    session, session_source, session_malformed = classify_session(str(body.get("session", SessionMode.RESUME.value)))
    checks = [str(c) for c in _as_list(body.get("checks", []))]
    produces = [str(p) for p in _as_list(body.get("produces", []))]
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
    )


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


@dataclass(frozen=True)
class Edge:
    """A directed, choice-keyed connection out of one node.

    ``to_node_name`` is a node name of this graph, the reserved terminal, or — for a
    cross-graph migration edge (issue #90) — the raw ``graph:<name>`` string (the target
    is re-derived from it on load via :func:`target_graph_of`, so no separate column is
    persisted). ``target_graph`` is that parsed name when the edge is cross-graph
    (``None`` for a same-graph/terminal edge); ``model`` is the optional per-choice model
    override applied when the migration re-pins the chunk (``None`` keeps its model)."""

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
    produces: list[str]
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
    # The targeted-resume source node name (issue #115) — see ``NodeDoc.session_source``.
    # ``None`` means "chunk most-recent" (bare ``resume``) or ``fresh``; a validated graph
    # never carries a malformed session, so there is no ``Node``-level malformed flag.
    session_source: str | None = None

    @property
    def is_hub_command_node(self) -> bool:
        """True for a generic hub command node (``executor: hub`` + a non-empty
        ``run:``) — the shape :class:`~blizzard.hub.delivery.hub_node.HubNodeExecutor`
        drives. False for every worker node. Since #67 every hub node authors
        ``run:`` — there is no other kind left, the special-cased deliver node is
        retired — but the property stays a plain predicate rather than an
        assertion, since an author is free to declare a (currently pointless)
        ``executor: hub`` node with an empty ``run:``."""
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

    Keyed by ``graph_id``. Encodes the same "newest-per-name, retired excluded" rule
    :meth:`IReadGraphRepository.get_enabled_by_name` applies at lookup time — a pure
    domain function so the read-listing surface (``GET /graphs``) does not re-derive
    it at the edge (``bzh:domain-core``). ``retired_ids`` names every ``graph_id``
    whose newest lifecycle fact (issue #101) reads retired; a retired graph is never a
    candidate, so a name whose every graph is retired marks none of them effective.

    Required, keyword-only, and carries no default (issue #101 lockstep note): this
    must stay in lockstep with ``get_enabled_by_name``'s own retired-exclusion, and a
    caller that forgot the argument silently getting the pre-#101 "every graph is a
    candidate" behavior back — with no type error — is exactly the kind of drift that
    would undo. Pass ``retired_ids=frozenset()`` explicitly for the pre-#101 behavior.
    """
    newest_by_name: dict[str, Graph] = {}
    for graph in graphs:
        if graph.graph_id in retired_ids:
            continue
        current = newest_by_name.get(graph.name)
        # Tie-break on graph_id descending (ULIDs sort lexically by creation) — kept in
        # lockstep with IReadGraphRepository.get_enabled_by_name's ORDER BY.
        if current is None or (graph.created_at, graph.graph_id) > (current.created_at, current.graph_id):
            newest_by_name[graph.name] = graph
    effective_ids = {g.graph_id for g in newest_by_name.values()}
    return {g.graph_id: g.graph_id in effective_ids for g in graphs}


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


class IReadGraphRepository(Protocol):
    """Read-only graph access. Controllers at the edges depend on this variant."""

    def get(self, graph_id: str) -> Graph | None: ...
    def get_enabled_by_name(self, name: str) -> Graph | None:
        """The newest non-retired graph with ``name`` — the default-graph pin lookup.

        Excludes every retired ``graph_id`` (issue #101); resolves to ``None`` when
        ``name``'s every minted graph is retired, kept in lockstep with
        :func:`mark_effective`.
        """
        ...

    def list_all(self) -> list[Graph]: ...

    def is_retired(self, graph_id: str) -> bool:
        """Whether ``graph_id``'s newest lifecycle fact reads retired (issue #101).

        ``False`` for a ``graph_id`` with no lifecycle fact at all — every freshly
        minted graph starts enabled.
        """
        ...

    def retired_graph_ids(self) -> set[str]:
        """Every ``graph_id`` whose newest lifecycle fact reads retired (issue #101).

        The set :func:`mark_effective` excludes from candidacy — the bulk counterpart
        to :meth:`is_retired`, used by the ``GET /graphs`` listing so it derives
        ``effective`` once rather than per-row.
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
