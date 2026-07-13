"""Workflow-graph domain model — the definition chunks travel (D-025/D-071).

Two representations live here, and the split is deliberate:

* the **authoring doc** (:class:`GraphDoc` and friends) — the plain-data shape a
  ``POST /graphs`` YAML body parses into, before any ids are minted. It is what
  the mint-time validator (:mod:`blizzard.hub.domain.graph_validation`) checks.
* the **reified graph** (:class:`Graph`, :class:`Node`, :class:`Choice`,
  :class:`Edge`) — the immutable, id-carrying entities a validated doc compiles
  into at mint (D-033), and what the hub store persists.

Everything here is a dependency-free domain type (``bzh:domain-core``): no YAML, no
SQLAlchemy, no FastAPI. Parsing YAML text into a ``dict`` and inlining prompt
*file* references are edge concerns done before :func:`parse_graph_doc` and the
validator run — the domain sees only already-loaded data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

# The reserved terminal a choice may point at instead of a node name (D-071).
RESERVED_TERMINAL = "done"


class Executor(StrEnum):
    """Where a node's step runs (D-030)."""

    RUNNER = "runner"
    HUB = "hub"


class JudgedBy(StrEnum):
    """Who renders a node's exit judgement — the structural gate marker (D-041)."""

    WORKER = "worker"
    HUMAN = "human"


class SessionMode(StrEnum):
    """Per-node session freshness (D-054)."""

    RESUME = "resume"
    FRESH = "fresh"


class DeliverMode(StrEnum):
    """The deliver hub-node's landing mode (D-059)."""

    MERGE_TO_MAIN = "merge-to-main"
    OPEN_PR = "open-pr"


class RetriesExhausted(StrEnum):
    """The only exhaustion target in the MVP (D-071)."""

    ESCALATE = "escalate"


# Machinery-defined outcomes per hub executor (D-071/D-086): a hub node omits its
# judgement to accept these defaults, or authors a matching choice to override one.
# Keyed by node *name* in the thin slice — deliver is the only hub node.
HUB_NODE_OUTCOMES: dict[str, frozenset[str]] = {
    "deliver": frozenset({"landed", "conflict"}),
}


# --- Authoring doc (parsed from a YAML body, pre-mint) ----------------------


@dataclass(frozen=True)
class ChoiceDoc:
    """One fused choice/edge entry as authored (D-071)."""

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
        judgement=_parse_judgement(body.get("judgement")),
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
    """One selectable outcome of one node's judgement (D-042)."""

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
class Node:
    """One station in one immutable graph (D-033)."""

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
    choices: list[Choice] = field(default_factory=list)


@dataclass(frozen=True)
class Graph:
    """A reified, immutable workflow graph (D-025/D-033)."""

    graph_id: str
    name: str
    entry_node_id: str
    nodes: list[Node]
    edges: list[Edge]

    def node_by_name(self, name: str) -> Node | None:
        return next((n for n in self.nodes if n.name == name), None)

    def node_by_id(self, node_id: str) -> Node | None:
        return next((n for n in self.nodes if n.node_id == node_id), None)
