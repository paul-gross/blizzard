"""Workflow-graph domain model — the definition chunks travel.

Two representations: the **authoring doc** (:class:`GraphDoc` and friends), a plain-data
shape carrying no ids, and the **reified graph** (:class:`Graph`, :class:`Node`,
:class:`Choice`, :class:`Edge`), the immutable id-carrying entities a validated doc
compiles into at mint. Every type is dependency-free (``bzh:domain-core``)."""

from __future__ import annotations

from collections.abc import Buffer, Collection, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, SupportsIndex, SupportsInt, TypeGuard

from blizzard import __version__ as HUB_VERSION
from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.graph_lifecycle import GraphLifecycle
from blizzard.foundation.graph_targets import ChoiceTargetKind
from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.foundation.roles import domain_model


class GraphParseError(ValueError):
    """A graph definition is structurally malformed (before validation)."""


def _retry_max(value: object) -> int:
    if isinstance(value, (str, Buffer, SupportsInt, SupportsIndex)):
        return int(value)
    raise TypeError(
        f"int() argument must be a string, a bytes-like object or a real number, not '{type(value).__name__}'"
    )


def _is_mapping(value: object) -> TypeGuard[dict[object, object]]:
    return isinstance(value, dict)


def _is_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


class TargetGraphRetired(Exception):
    """A graph named explicitly as a new target has since been retired."""

    def __init__(self, graph_id: str) -> None:
        super().__init__(f"graph {graph_id} is retired and cannot receive new work")
        self.graph_id = graph_id


# The reserved terminal a choice may point at instead of a node name.
RESERVED_TERMINAL = "done"

# The cross-graph target prefix: a ``to: graph:<name>`` choice re-pins the
# chunk rather than transitioning it (``bzh:migration-not-transition``).
GRAPH_TARGET_PREFIX = "graph:"


@domain_model
@dataclass(frozen=True)
class ChoiceTarget:
    """What a choice's ``to:`` value points at — pure syntax.

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
    def malformed(self) -> bool:  # ast-grep-ignore: bzh:property-delegates
        return self.node is None and self.graph is None

    def kind(self) -> ChoiceTargetKind:
        """``graph`` for a cross-graph target, ``done`` for the reserved terminal, else
        ``node`` — a malformed target included, which validation refuses before any mint."""
        if self.graph is not None:
            return ChoiceTargetKind.GRAPH
        if self.node == RESERVED_TERMINAL:
            return ChoiceTargetKind.DONE
        return ChoiceTargetKind.NODE


# ``session: resume:<name>`` — resume ``<name>``'s most-recent
# session rather than the chunk's most-recent overall.
SESSION_RESUME_TARGET_PREFIX = "resume:"

# ``session: fresh:<name>`` — mint a session and make it ``<name>``'s new
# head, which a later ``resume:<name>`` member continues.
SESSION_FRESH_TARGET_PREFIX = "fresh:"

# The one owner of the legal-form vocabulary quoted back at an author (``bzh:one-owner``).
SESSION_LEGAL_FORMS = "`fresh`, `resume`, `resume:<node>`, `fresh:<session>`, or `resume:<session>`"


@domain_model
@dataclass(frozen=True)
class SessionRef:
    """What an authored ``session:`` value names — pure syntax.

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
# exit 0 -> success, nonzero -> failure. Reserved names whose edges a node authors:
# every hub node authors `failure`, and `success` is opt-in.
HUB_DEFAULT_SUCCESS_CHOICE = "success"
HUB_DEFAULT_FAILURE_CHOICE = "failure"

# The default kick-back cap a hub node omitting ``bounce_cap`` tolerates before the
# chunk escalates (#64). Per-node, not global.
DEFAULT_BOUNCE_CAP = 5


@domain_model
@dataclass(frozen=True)
class Parser:
    """One authored mapping, read through the coercions the doc types share.

    Structural coercion only — a shape that cannot be read raises
    :class:`GraphParseError`; whether a readable value is *legal* is the validator's."""

    body: dict[object, object]

    @classmethod
    def of(cls, value: object, where: object) -> Parser:
        if not _is_mapping(value):
            raise GraphParseError(f"expected a map at {where!r}, got {type(value).__name__}")
        body: dict[object, object] = value
        return cls(body)

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
        if not _is_list(value):
            raise GraphParseError(f"expected a list, got {type(value).__name__}")
        items: list[object] = value
        return items


# --- Authoring doc (parsed from a YAML body, pre-mint) ----------------------


@domain_model
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
    # Gated on green checks: unroutable while any of its node's is red.
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


@domain_model
@dataclass(frozen=True)
class JudgementDoc:
    prompt: str | None
    by: JudgedBy
    choices: list[ChoiceDoc]

    @classmethod
    def of(cls, raw: object) -> JudgementDoc | None:
        if raw is None:
            return None
        if not _is_mapping(raw):
            raise GraphParseError("`judgement` must be a map")
        mapping: dict[object, object] = raw
        body = Parser(mapping)
        by = JudgedBy(str(body.get("by", JudgedBy.WORKER.value)))
        choices_raw = body.get("choices", {})
        if not _is_mapping(choices_raw):
            raise GraphParseError("`judgement.choices` must be a map of choice name -> entry")
        choices: dict[object, object] = choices_raw
        return cls(
            prompt=body.text("prompt"),
            by=by,
            choices=[ChoiceDoc.of(name, entry) for name, entry in choices.items()],
        )


@domain_model
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


@domain_model
@dataclass(frozen=True)
class ProducesSpec:
    """One ``produces:`` entry, kind-carrying.

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


class NodeShape:
    """What the authored and the reified node share — the one owner of the predicate
    keyed on that pair."""

    executor: Executor

    @property
    def is_hub_command_node(self) -> bool:  # ast-grep-ignore: bzh:property-delegates
        """True for every hub node — one naming an action, and one stored naming none."""
        return self.executor is Executor.HUB


@domain_model
@dataclass(frozen=True)
class NodeDoc(NodeShape):
    name: str
    executor: Executor
    prompt: str | None
    checks: list[str]
    produces: list[ProducesSpec]
    session: SessionMode
    retries_max: int | None
    retries_exhausted: str | None
    judgement: JudgementDoc | None
    # The kick-back cap (#64) — ``None`` accepts ``DEFAULT_BOUNCE_CAP``.
    bounce_cap: int | None = None
    # The hub command node's declared commands (#65, #67); empty on a worker node.
    run: list[RunStepDoc] = field(default_factory=list[RunStepDoc])
    # Whether the node declares ``run:`` at all, an empty list included — what mint refuses.
    names_run: bool = False
    # The hub action this node names, in place of ``run:``, and its ``with:`` arguments,
    # each one value or a list read as a tuple.
    action: str | None = None
    action_args: Mapping[str, tuple[str, ...]] = field(default_factory=dict[str, tuple[str, ...]])
    # The ``with:`` keys whose value is neither a string nor a list of strings — recorded,
    # not refused, so validation can name each one.
    malformed_action_args: tuple[str, ...] = ()
    # The pending-poll cadence (#66), in seconds — ``None`` accepts the executor default.
    poll_interval_seconds: int | None = None
    poll_timeout_seconds: int | None = None
    # See :class:`SessionRef`, whose fields these carry.
    session_source: str | None = None
    session_malformed: bool = False
    # Where ``checks:`` run — ``None`` runs them at the binding workdir's root.
    checks_cwd: str | None = None
    # The per-check timeout, in seconds; a timeout is a red check.
    checks_timeout: int | None = None
    # The retired keys this node still declares — recorded, not refused, so validation
    # can name each one.
    retired_keys: tuple[str, ...] = ()

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
        action_args, malformed_action_args = cls._action_args(body.get("with"), node=name)
        return cls(
            name=name,
            executor=executor,
            prompt=body.text("prompt"),
            checks=checks,
            produces=produces,
            session=session.mode,
            retries_max=retries_max,
            retries_exhausted=retries_exhausted,
            bounce_cap=bounce_cap,
            judgement=JudgementDoc.of(body.get("judgement")),
            run=run,
            names_run="run" in body.body,
            action=body.text("action"),
            action_args=action_args,
            malformed_action_args=malformed_action_args,
            poll_interval_seconds=poll_interval_seconds,
            poll_timeout_seconds=poll_timeout_seconds,
            session_source=session.source,
            session_malformed=session.malformed,
            checks_cwd=checks_cwd,
            checks_timeout=checks_timeout,
            retired_keys=tuple(sorted(key for key in RETIRED_NODE_KEYS if key in body.body)),
        )

    @staticmethod
    def _action_args(raw: object, *, node: str) -> tuple[dict[str, tuple[str, ...]], tuple[str, ...]]:
        if raw is None:
            return {}, ()
        body = Parser.of(raw, f"node {node!r} `with`").body
        args: dict[str, tuple[str, ...]] = {}
        malformed: list[str] = []
        for key, value in body.items():
            if isinstance(value, str):
                args[str(key)] = (value,)
            elif _is_list(value):
                values: list[object] = value
                if all(isinstance(v, str) for v in values):
                    args[str(key)] = tuple(v for v in values if isinstance(v, str))
                else:
                    malformed.append(str(key))
            else:
                items = cast(list[object], value) if isinstance(value, list) else None
                strings = [v for v in items if isinstance(v, str)] if items is not None else None
                if strings is not None and len(strings) == len(items or ()):
                    args[str(key)] = tuple(strings)
                else:
                    malformed.append(str(key))
        return args, tuple(sorted(malformed))

    @staticmethod
    def _retries(raw: object) -> tuple[int | None, str | None]:
        if not _is_mapping(raw):
            return (None, None)
        mapping: dict[object, object] = raw
        raw_max = mapping.get("max")
        raw_exhausted = mapping.get("exhausted")
        return (
            _retry_max(raw_max) if raw_max is not None else None,
            str(raw_exhausted) if raw_exhausted is not None else None,
        )


@domain_model
@dataclass(frozen=True)
class RotatePolicy:
    """One declared session's rotation bounds.

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


# Node keys an earlier hub read and this one refuses at mint.
RETIRED_NODE_KEYS = frozenset({"proposes_work_items"})


# Every key this hub's session parser reads by name.
SESSION_KNOWN_KEYS = frozenset({"model", "effort", "rotate", "compaction_window", "harnesses"})


@domain_model
@dataclass(frozen=True)
class SessionDecl:
    """One graph-level named session declaration.

    Carries workflow *policy* only (``bzh:app-agnostic-graphs``); ``model``, ``effort``,
    ``compaction_window``, and ``harnesses`` (its own acceptable set) are opaque to the hub."""

    name: str
    model: list[str] = field(default_factory=list[str])
    effort: str | None = None
    rotate: RotatePolicy | None = None
    compaction_window: str | None = None
    harnesses: list[str] = field(default_factory=list[str])

    @classmethod
    def of(cls, key: object, raw: object) -> SessionDecl:
        body = Parser.of(raw, f"session {key!r}")
        name = str(key)
        unknown_keys = [key for key in body.body if key not in SESSION_KNOWN_KEYS]
        if any(not isinstance(key, str) for key in unknown_keys):
            raise TypeError("session keys must be strings")
        unknown = sorted(key for key in unknown_keys if isinstance(key, str))
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
        raw_harnesses = body.get("harnesses")
        # An authored empty list names an acceptable set of nothing, unlike an omitted key —
        # so it is rejected here rather than silently accepted as the same empty shape.
        if isinstance(raw_harnesses, list) and not raw_harnesses:
            raise GraphParseError(
                f"session {name!r}: `harnesses` must not be an empty list — omit the key to accept every harness"
            )
        harnesses = (
            [str(raw_harnesses)] if isinstance(raw_harnesses, str) else [str(h) for h in body.items("harnesses")]
        )
        raw_rotate = body.get("rotate")
        return cls(
            name=name,
            model=model,
            effort=body.text("effort"),
            rotate=RotatePolicy.of(raw_rotate, session=name) if raw_rotate is not None else None,
            compaction_window=body.text("compaction_window"),
            harnesses=harnesses,
        )


@domain_model
@dataclass(frozen=True)
class GraphDoc:
    """A whole graph definition as authored — the validator's input."""

    name: str
    entry: str
    nodes: list[NodeDoc]
    # The graph-level named-session declarations, keyed by name — a top-level
    # sibling of ``nodes:``, empty for every graph that declares none.
    sessions: dict[str, SessionDecl] = field(default_factory=dict[str, SessionDecl])
    # The graph-scoped `artifacts:` map, name -> baked content — a top-level sibling of
    # `nodes:`/`sessions:`. Every value is already inlined text by the time this parses.
    artifacts: dict[str, str] = field(default_factory=dict[str, str])

    @classmethod
    def of(cls, raw: dict[str, object]) -> GraphDoc:
        """Parse a plain ``dict`` (a decoded config document) into a whole doc."""
        try:
            name = str(raw["name"])
            entry = str(raw["entry"])
            nodes_raw = raw["nodes"]
        except KeyError as exc:
            raise GraphParseError(f"graph definition missing required key: {exc}") from exc
        if not _is_mapping(nodes_raw):
            raise GraphParseError("`nodes` must be a map of node name -> node")
        nodes: dict[object, object] = nodes_raw
        return cls(
            name=name,
            entry=entry,
            nodes=[NodeDoc.of(node_name, body) for node_name, body in nodes.items()],
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

    def differs_from(self, minted: GraphDoc | None) -> bool:
        """Whether this definition is a new graph against ``minted``, the newest of its name —
        compared parsed, so a reorder or a comment alone is no change; nothing minted yet is one."""
        return minted is None or minted != self

    def node(self, name: str) -> NodeDoc | None:
        return next((n for n in self.nodes if n.name == name), None)


# --- Reified graph (post-mint, id-carrying, immutable) ----------------------


@domain_model
@dataclass(frozen=True)
class Choice:
    """One selectable outcome of one node's judgement."""

    choice_id: str
    name: str
    description: str
    # Gated on green checks — see ``ChoiceDoc.requires_checks``.
    requires_checks: bool = False


@domain_model
@dataclass(frozen=True)
class Edge:
    """A directed, choice-keyed connection out of one node.

    ``target_graph`` is ``to_node_name``'s parsed name when the edge is cross-graph;
    ``model`` overrides the chunk's model when the migration re-pins it."""

    from_node_id: str
    choice_id: str
    to_node_name: str  # a node name of this graph, RESERVED_TERMINAL, or ``graph:<name>``
    prompt_addendum: str | None = None
    target_graph: str | None = None
    model: str | None = None


@domain_model
@dataclass(frozen=True)
class Node(NodeShape):
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
    judgement_prompt: str | None = None
    choices: list[Choice] = field(default_factory=list[Choice])
    # The kick-back cap (#64) — ``None`` accepts ``DEFAULT_BOUNCE_CAP``.
    bounce_cap: int | None = None
    # See ``NodeDoc.action`` / ``NodeDoc.action_args``.
    action: str | None = None
    action_args: Mapping[str, tuple[str, ...]] = field(default_factory=dict[str, tuple[str, ...]])
    # The stored command a hub node carried over with no action still runs, verbatim; never set at mint.
    legacy_run: str | None = None
    # The pending-poll cadence (#66), in seconds — see ``NodeDoc.poll_interval_seconds``.
    poll_interval_seconds: int | None = None
    poll_timeout_seconds: int | None = None
    # A validated graph never carries a malformed session, so no malformed flag here.
    session_source: str | None = None
    # See ``NodeDoc.checks_cwd`` / ``NodeDoc.checks_timeout``.
    checks_cwd: str | None = None
    checks_timeout: int | None = None


@domain_model
@dataclass(frozen=True)
class GraphArtifact:
    """One graph-scoped artifact baked into the mint — its authored name, the
    loader-inlined content, and its authored-order position."""

    name: str
    content: str
    ordinal: int


@domain_model
@dataclass(frozen=True)
class Graph:
    graph_id: str
    name: str
    entry_node_id: str
    nodes: list[Node]
    edges: list[Edge]
    created_at: datetime
    # The graph-level named-session declarations, in authored order.
    sessions: list[SessionDecl] = field(default_factory=list[SessionDecl])
    # The graph-scoped `artifacts:` declarations, in authored order.
    artifacts: list[GraphArtifact] = field(default_factory=list[GraphArtifact])

    @property
    def declares_git_commit(self) -> bool:  # ast-grep-ignore: bzh:property-delegates
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

    def cross_graph_targets(self) -> list[str]:
        """Every distinct ``graph:<name>`` target this graph's edges name, in edge order."""
        return list(dict.fromkeys(e.target_graph for e in self.edges if e.target_graph is not None))

    def cross_graph_target_name(self, from_node_id: str, choice_name: str) -> str | None:
        """The graph name a choice submitted from ``from_node_id`` migrates to — ``None`` when the
        node is unknown, the choice keys no edge, or its edge is not cross-graph."""
        if self.node_by_id(from_node_id) is None:
            return None
        edge = self.edge_for_choice(from_node_id, choice_name)
        return None if edge is None else edge.target_graph

    def lifecycle_fact(self, *, retired: bool, at: datetime, by: str) -> GraphLifecycleFact:
        """The fact a retire (``retired=True``) or enable appends (:data:`GRAPH_TRANSITIONS`)."""
        return GraphLifecycleFact(graph_id=self.graph_id, retired=retired, at=at, by=by)

    def policy_fact(self, *, follow_latest: bool | None, at: datetime, by: str) -> GraphPolicyFact:
        """The fact setting this mint's follow-latest tri-state appends — ``None`` reverts to the
        hub default (:data:`GRAPH_TRANSITIONS`)."""
        return GraphPolicyFact(graph_id=self.graph_id, follow_latest=follow_latest, at=at, by=by)


@domain_model
@dataclass(frozen=True)
class GraphLifecycleFact:
    """One appended retire or enable fact over a graph id."""

    graph_id: str
    retired: bool
    at: datetime
    by: str


@domain_model
@dataclass(frozen=True)
class GraphPolicyFact:
    """One appended follow-latest policy fact over a graph id."""

    graph_id: str
    follow_latest: bool | None
    at: datetime
    by: str


# --- Graph lifecycle (which verbs are legal from which state) ---------------


class GraphState(StrEnum):
    """A graph's lifecycle state: the newest retire/enable fact wins, and none reads enabled.
    The follow-latest policy is orthogonal to it."""

    ENABLED = "enabled"
    RETIRED = "retired"

    @classmethod
    def of(cls, retired: bool) -> GraphState:
        return cls.RETIRED if retired else cls.ENABLED


class GraphVerb(StrEnum):
    RETIRE = "retire"
    ENABLE = "enable"
    SET_FOLLOW_LATEST = "set_follow_latest"
    #: Resolution by name — a ``graph:<name>`` edge, a routine, the default graph.
    RESOLVE_BY_NAME = "resolve_by_name"
    #: Naming the graph explicitly as a new target.
    TARGET = "target"
    #: Running a chunk already pinned to the graph.
    RUN_PINNED = "run_pinned"


class GraphVerdict(StrEnum):
    LEGAL = "legal"
    REFUSED = "refused"


#: Which verbs are legal from which graph state; a repeated verb appends a duplicate fact, never a no-op.
GRAPH_TRANSITIONS: Mapping[GraphState, Mapping[GraphVerb, GraphVerdict]] = MappingProxyType(
    {
        GraphState.ENABLED: MappingProxyType(dict.fromkeys(GraphVerb, GraphVerdict.LEGAL)),
        GraphState.RETIRED: MappingProxyType(
            {
                GraphVerb.RETIRE: GraphVerdict.LEGAL,
                GraphVerb.ENABLE: GraphVerdict.LEGAL,
                GraphVerb.SET_FOLLOW_LATEST: GraphVerdict.LEGAL,
                GraphVerb.RESOLVE_BY_NAME: GraphVerdict.REFUSED,
                GraphVerb.TARGET: GraphVerdict.REFUSED,
                GraphVerb.RUN_PINNED: GraphVerdict.LEGAL,
            }
        ),
    }
)


@domain_model
@dataclass(frozen=True)
class GraphStanding:
    """A loaded graph with its lifecycle state — what a decision on the graph's state takes."""

    graph: Graph
    retired: bool

    @property
    def state(self) -> GraphState:
        return GraphState.of(self.retired)

    def verdict(self, verb: GraphVerb) -> GraphVerdict:
        return GRAPH_TRANSITIONS[self.state][verb]

    def targetable(self) -> bool:
        """Whether the graph may be named as a new target."""
        return self.verdict(GraphVerb.TARGET) is GraphVerdict.LEGAL

    def require_targetable(self) -> None:
        """Refuse naming a retired graph explicitly as a new target."""
        if not self.targetable():
            raise TargetGraphRetired(self.graph.graph_id)


@domain_model
@dataclass(frozen=True)
class GraphSummary:
    """One minted graph's listing-shape fields — :meth:`IReadGraphRepository.list_summaries`'s
    narrow sibling of a fully reified :class:`Graph`, with no nodes/edges/sessions/artifacts."""

    graph_id: str
    name: str
    entry_node_id: str
    created_at: datetime


# --- Mint selection (which mint of a name a chunk sees) ---------------------


@domain_model
@dataclass(frozen=True)
class Mint:
    """One minted graph, ordered by when it was minted. Holds only a
    :class:`GraphSummary` — the ``graph_id``/``name``/``created_at`` fields this reads
    — never a fully reified :class:`Graph`."""

    graph: GraphSummary

    @classmethod
    def of(cls, graph: Graph | GraphSummary) -> Mint:
        if isinstance(graph, GraphSummary):
            return cls(graph)
        return cls(
            GraphSummary(
                graph_id=graph.graph_id, name=graph.name, entry_node_id=graph.entry_node_id, created_at=graph.created_at
            )
        )

    @property
    def order(self) -> tuple[datetime, str]:
        # Tie-break on graph_id descending — ULIDs sort lexically by creation.
        return (self.graph.created_at, self.graph.graph_id)

    def newer_than(self, other: Mint) -> bool:
        """**Strictly** newer — pinned by
        tests/test_follow_latest_policy.py::test_the_policy_never_drags_a_chunk_backwards_onto_an_older_mint"""
        return self.order > other.order


@domain_model
@dataclass(frozen=True)
class Mints:
    """Every mint of every name, with the retired ones out of contention."""

    mints: list[Mint]
    retired_ids: Collection[str]

    @classmethod
    def of(cls, graphs: Sequence[Graph | GraphSummary], *, retired_ids: Collection[str]) -> Mints:
        return cls([Mint.of(g) for g in graphs], retired_ids)

    @property
    def newest_by_name(self) -> dict[str, Mint]:  # ast-grep-ignore: bzh:property-delegates
        newest: dict[str, Mint] = {}
        for candidate in self.mints:
            if candidate.graph.graph_id in self.retired_ids:
                continue
            current = newest.get(candidate.graph.name)
            if current is None or candidate.newer_than(current):
                newest[candidate.graph.name] = candidate
        return newest

    @property
    def effective(self) -> dict[str, bool]:  # ast-grep-ignore: bzh:property-delegates
        """Whether each ``graph_id`` is the newest non-retired mint of its name.

        ``retired_ids`` carries no default, so omitting it raises — pinned by
        tests/test_graph_domain.py::test_mints_effective_requires_retired_ids_explicitly"""
        effective_ids = {m.graph.graph_id for m in self.newest_by_name.values()}
        return {m.graph.graph_id: m.graph.graph_id in effective_ids for m in self.mints}

    @property
    def lifecycle(self) -> dict[str, GraphLifecycle]:  # ast-grep-ignore: bzh:property-delegates
        """Each ``graph_id``'s standing in its name's lineage — the one fold the listing's
        ``lifecycle`` and :attr:`effective` agree on."""
        effective = self.effective
        return {
            graph_id: (
                GraphLifecycle.RETIRED
                if graph_id in self.retired_ids
                else GraphLifecycle.EFFECTIVE
                if is_effective
                else GraphLifecycle.SUPERSEDED
            )
            for graph_id, is_effective in effective.items()
        }


@domain_model
@dataclass(frozen=True)
class FollowLatest:
    """Whether a chunk pinned to a graph follows the newest mint of its name.

    ``hub_default`` carries no default, so omitting it raises — pinned by
    tests/test_pin_hub_domain.py::test_follow_latest_requires_hub_default_explicitly"""

    enabled: bool

    @classmethod
    def of(cls, graph_policy: bool | None, *, hub_default: bool) -> FollowLatest:
        return cls(hub_default if graph_policy is None else graph_policy)

    def target(self, current: Graph, newest_enabled: Graph | None) -> Graph | None:
        """The mint a chunk pinned to ``current`` drifts to: ``newest_enabled``, the newest enabled
        mint of its name, only while the policy is on and it is **strictly** newer — never backwards."""
        if not self.enabled or newest_enabled is None:
            return None
        return newest_enabled if Mint.of(newest_enabled).newer_than(Mint.of(current)) else None


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


class IReadManyGraphs(Protocol):
    """The plural reifying read of graphs, composed into :class:`IReadGraphRepository`."""

    def get_many(self, graph_ids: Sequence[str]) -> dict[str, Graph]:
        """``{graph_id: Graph}`` for every requested id that exists, each reified in full
        (nodes, edges, sessions, artifacts) — :meth:`IReadGraphRepository.get`'s batched
        sibling. An id that doesn't exist is silently dropped, the same as ``get``
        returning ``None`` for it."""
        ...


class IReadGraphRepository(IReadManyGraphs, Protocol):
    """Read-only graph access."""

    def get(self, graph_id: str) -> Graph | None: ...
    def get_enabled_by_name(self, name: str) -> Graph | None:
        """The newest non-retired graph with ``name`` — the default-graph pin lookup.

        Excludes every retired ``graph_id``, so a name whose every mint is
        retired resolves to ``None``."""
        ...

    def enabled_names(self, names: Sequence[str]) -> set[str]:
        """Those of ``names`` with an enabled mint — the names :meth:`get_enabled_by_name` resolves, answered
        without reifying a graph (``bzh:bulk-reconstitution``)."""
        ...

    def any_minted(self, name: str) -> bool:
        """Whether any graph of ``name`` has ever been minted, retired or not
        — a cheap existence probe, in place of a full listing, to check membership by
        name."""
        ...

    def newest_definition_yaml(self, name: str) -> str | None:
        """The newest-minted graph of ``name``'s source YAML, ``None`` if never minted.

        Newest-*minted*, not newest-enabled: retirement does not change what was minted,
        so it is not consulted here.
        """
        ...

    def is_retired(self, graph_id: str) -> bool:
        """Whether ``graph_id``'s newest lifecycle fact reads retired.

        ``False`` for a ``graph_id`` with no lifecycle fact at all — every freshly
        minted graph starts enabled.
        """
        ...

    def retired_graph_ids(self) -> set[str]:
        """Every ``graph_id`` whose newest lifecycle fact reads retired.

        The set :class:`Mints` excludes from candidacy — the bulk counterpart to
        :meth:`is_retired`.
        """
        ...

    def follow_latest(self, graph_id: str) -> bool | None:
        """This graph's own follow-latest policy — the stored tri-state.

        ``None`` — the value for a graph with no policy fact — inherits the hub-level
        setting (:class:`FollowLatest`). Newest-fact-wins.
        """
        ...

    def load_graph_summaries(self, graph_ids: Sequence[str]) -> dict[str, GraphSummary]:
        """``{graph_id: GraphSummary}`` for every requested id that exists — a narrow
        projection that never reifies a whole :class:`Graph`, for a caller priming several
        ids' summaries at once. The entry node rides along on :class:`GraphSummary`
        itself, so a caller needing it never reifies for that alone."""
        ...

    def load_node_names(self, graph_ids: Sequence[str]) -> dict[str, dict[str, str]]:
        """``{graph_id: {node_id: name}}`` for every node belonging to any of the
        requested graphs — the node-level sibling of :meth:`load_graph_summaries`, keyed
        per graph so a node id is never looked up against the wrong one."""
        ...

    def list_summaries(self) -> list[GraphSummary]:
        """Every minted graph's listing-shape fields, newest first, reading only what a
        summary needs off the ``graphs`` table itself, with no per-graph fan-out."""
        ...

    def graph_id_of_enabled_name(self, name: str) -> str | None:
        """:meth:`get_enabled_by_name`'s narrow sibling — just the ``graph_id`` of the
        newest non-retired graph with ``name``, without reifying it."""
        ...


class ILockedGraphNameRead(Protocol):
    """The guard reads a name-keyed graph decision consults, resolved on the one connection its
    name lock was taken on (``bzh:store-exclusive-write``) — so neither can race the mint it
    precedes."""

    def get_enabled_by_name(self, name: str) -> Graph | None:
        """:meth:`IReadGraphRepository.get_enabled_by_name`, read under the lock."""
        ...

    def any_minted(self, name: str) -> bool:
        """:meth:`IReadGraphRepository.any_minted`, read under the lock."""
        ...


class IWriteGraphRepository(IReadGraphRepository, Protocol):
    """Adds the graph writes."""

    def mint(self, graph: Graph, *, definition_yaml: str, at: datetime) -> None:
        """Persist a reified, immutable graph and its source YAML."""
        ...

    def locked_name(self, name: str) -> AbstractContextManager[ILockedGraphNameRead]:
        """Lock graph ``name``'s key, first, then yield the read handle bound to that one
        connection — for a decision whose race has no graph row yet to lock
        (``bzh:store-exclusive-write``). A write under the lock goes through
        :meth:`mint_locked`, taking this same handle."""
        ...

    def mint_locked(self, handle: ILockedGraphNameRead, graph: Graph, *, definition_yaml: str, at: datetime) -> None:
        """:meth:`mint`, on ``handle``'s already-locked connection."""
        ...

    def record_lifecycle(self, graph_id: str, *, retired: bool, at: datetime, by: str) -> None:
        """Append a ``graph.retired``/``graph.enabled`` fact — newest-fact-wins.

        Never touches the ``graphs`` row itself — it stays insert-only and immutable.
        """
        ...

    def record_policy(self, graph_id: str, *, follow_latest: bool | None, at: datetime, by: str) -> None:
        """Append a follow-latest policy fact — newest-fact-wins.

        ``follow_latest=None`` is a real, recordable value — "revert to inheriting",
        not "leave unchanged". Never touches the ``graphs`` row.
        """
        ...
