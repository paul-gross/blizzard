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

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Protocol

# The reserved terminal a choice may point at instead of a node name.
RESERVED_TERMINAL = "done"


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
    """One fused choice/edge entry as authored."""

    name: str
    description: str | None
    to: str | None
    prompt_addendum: str | None = None


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
    session = SessionMode(str(body.get("session", SessionMode.RESUME.value)))
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
    return ChoiceDoc(
        name=name,
        description=str(description) if description is not None else None,
        to=str(to) if to is not None else None,
        prompt_addendum=str(addendum) if addendum is not None else None,
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
    """A directed, choice-keyed connection between two nodes of one graph."""

    from_node_id: str
    choice_id: str
    to_node_name: str  # a node name of this graph, or RESERVED_TERMINAL
    prompt_addendum: str | None = None


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


def mark_effective(graphs: list[Graph]) -> dict[str, bool]:
    """Mark the newest ``created_at`` graph per ``name`` as effective.

    Keyed by ``graph_id``. Encodes the same "newest-per-name" rule
    :meth:`IReadGraphRepository.get_enabled_by_name` applies at lookup time — a pure
    domain function so the read-listing surface (``GET /graphs``) does not re-derive
    it at the edge (``bzh:domain-core``).
    """
    newest_by_name: dict[str, Graph] = {}
    for graph in graphs:
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
        """The newest enabled graph with ``name`` — the default-graph pin lookup."""
        ...

    def list_all(self) -> list[Graph]: ...


class IWriteGraphRepository(IReadGraphRepository, Protocol):
    """Read-write graph access. Only the domain layer depends on this variant."""

    def mint(self, graph: Graph, *, definition_yaml: str, at: datetime) -> None:
        """Persist a reified, immutable graph and its source YAML."""
        ...
