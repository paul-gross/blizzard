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

from blizzard.hub.domain.artifacts import ArtifactKind

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
# session overall (bare ``resume``). The ``<name>`` may also name a graph-level **declared
# session** (a ``sessions:`` entry, issue #144) — resolution is declared-session-first,
# node-name-second, and belongs to the runner, not to this parser.
SESSION_RESUME_TARGET_PREFIX = "resume:"

# The prefix for a named-session fresh mint (issue #144): ``session: fresh:<name>`` always
# mints a session and makes it the named pool's new head, which a later ``resume:<name>``
# member continues. ``<name>`` must name a declared session — a node name would mean
# nothing here, since ``fresh`` always mints and a session minted at node Y is not in node
# X's implicit lineage (D1). The validator enforces that; this stays a pure parser.
SESSION_FRESH_TARGET_PREFIX = "fresh:"

# Every legal authored ``session:`` form, in the malformed-value error message's own words —
# one owner for the vocabulary the validator quotes back at an author (``bzh:one-owner``).
SESSION_LEGAL_FORMS = "`fresh`, `resume`, `resume:<node>`, `fresh:<session>`, or `resume:<session>`"


def classify_session(raw: str) -> tuple[SessionMode, str | None, bool]:
    """Classify a node's authored ``session:`` value into ``(mode, source, malformed)``
    (issues #115, #144) — a pure syntax parser, mirroring :func:`classify_choice_target`.

    - ``"resume"`` -> ``(RESUME, None, False)`` — resume the chunk's most-recent
      session (any node).
    - ``"resume:<name>"`` -> ``(RESUME, "<name>", False)`` — resume ``<name>``'s
      most-recent session, where ``<name>`` is a declared session (#144) or, failing
      that, a node (#115). ``<name>`` is carried verbatim; which of the two it names —
      and whether it names either — is the validator's job, exactly the
      parse-never-validates split :func:`classify_choice_target` already keeps.
    - ``"fresh"`` -> ``(FRESH, None, False)``.
    - ``"fresh:<name>"`` -> ``(FRESH, "<name>", False)`` — mint a fresh head of the
      declared session ``<name>`` (#144). Carried verbatim for the same reason.
    - anything else (``"resume:"`` / ``"fresh:"`` with an empty name, or an
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
    if raw.startswith(SESSION_FRESH_TARGET_PREFIX):
        name = raw[len(SESSION_FRESH_TARGET_PREFIX) :]
        if name:
            return (SessionMode.FRESH, name, False)
    return (SessionMode.RESUME, None, True)


class RetriesExhausted(StrEnum):
    """The only exhaustion target in the MVP."""

    ESCALATE = "escalate"


# The reserved default outcome names a hub command node's machinery maps a command's
# exit code to when the command prints no explicit choice (#65): exit 0 -> success,
# nonzero -> failure. A node authors a matching choice to route either default
# anywhere it likes, including straight to the reserved terminal — no node name is
# privileged by the engine (#67).
HUB_DEFAULT_SUCCESS_CHOICE = "success"
HUB_DEFAULT_FAILURE_CHOICE = "failure"

# The reserved **pending** outcome (#66) — a hub command node's ``run:`` step signals
# it by printing this literal name on its last stdout line (exit code 0; a nonzero
# exit is always a failure, never pending). Recognized regardless of whether the node
# authors a matching choice — like ``success``/``failure``, it is machinery-reserved,
# not an authored edge; the poll behavior it triggers belongs to
# ``blizzard.hub.delivery.hub_node``.
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
    # Whether this choice is gated on green checks (issue #114) — a worker may not route
    # through it while any of its node's `checks:` is red. The validator rejects it on a
    # choice whose node declares no `checks:`, and on hub/human-judged nodes.
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

    ``produces``, when set, names a marker artifact: the engine records it once this
    step exits 0, and SKIPS the step on any later re-run once it already exists — the
    at-least-once-per-step crash contract. ``name`` is a human label only (surfaced in
    logs/artifacts); it defaults to the step's 1-based position when omitted.
    """

    command: str
    name: str | None = None
    produces: str | None = None


@dataclass(frozen=True)
class ProducesSpec:
    """One ``produces:`` entry, kind-carrying (D1, issue #143).

    Authored as a bare string (``kind`` defaults to :attr:`ArtifactKind.ASSET`) or a
    mapping ``{name, kind}`` (a ``git_commit`` expectation). :func:`_parse_node`
    normalizes both authored forms to this one type, so every downstream reader sees a
    single shape regardless of which form the author wrote.
    """

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
    # The kick-back cap (#64) — ``None`` accepts the fleet default (``DEFAULT_BOUNCE_CAP``);
    # a hub node may author its own, stricter or looser.
    bounce_cap: int | None = None
    # The generic hub command node's declared commands (#65, #67) — non-empty exactly on
    # a node ``executor: hub`` authors as the generic primitive; empty on every worker node.
    run: list[RunStepDoc] = field(default_factory=list)
    # The pending-poll cadence (#66), in seconds — ``None`` accepts the executor's
    # own default (:data:`blizzard.hub.delivery.hub_node.DEFAULT_POLL_INTERVAL` /
    # ``DEFAULT_POLL_TIMEOUT``). Legal only on a generic hub command node
    # (``executor: hub`` with ``run:``) — a node with no ``pending``-reporting step
    # never reads either.
    poll_interval_seconds: int | None = None
    poll_timeout_seconds: int | None = None
    # The session reference target (issues #115, #144) — the parsed ``<name>`` of a
    # ``session: resume:<name>`` or ``session: fresh:<name>`` form, ``None`` for bare
    # ``resume``/``fresh``. Set by :func:`classify_session`; whether it names a declared
    # session or an existing node is the validator's job. Read with ``session`` beside it:
    # the same ``<name>`` means "resume this pool's head" under ``RESUME`` and "mint this
    # pool a new head" under ``FRESH``.
    session_source: str | None = None
    # Whether the authored ``session:`` value was structurally malformed (issues #115, #144) —
    # e.g. ``resume:``/``fresh:`` with an empty name, or an unrecognized token. Kept
    # separate from ``session_source`` (which is ``None`` in this case too) so the
    # validator can distinguish "malformed syntax" from "well-formed but names no node"
    # without re-parsing raw YAML (parse never validates, but the validator still needs
    # the parse's own verdict carried forward — ``bzh:one-owner``).
    session_malformed: bool = False
    # Where the runner runs this node's ``checks:`` (issue #114) — a path resolved
    # *relative to the leased env's binding workdir* (``join(binding.workdir, checks_cwd)``);
    # ``None`` runs them at the env workdir root. Meaningful only on a node with ``checks:``;
    # the validator rejects it otherwise. Per-check cwd is a documented deferral — one
    # node-level cwd keeps ``checks:`` a bare ``list[str]``.
    checks_cwd: str | None = None
    # The per-check timeout (issue #114), in seconds — ``None`` accepts the check-runner's
    # own default (:data:`blizzard.runner.loop.checks.DEFAULT_CHECK_TIMEOUT`). A timeout is
    # a red check. Meaningful only on a node with ``checks:``; the validator rejects it otherwise.
    checks_timeout: int | None = None


@dataclass(frozen=True)
class RotatePolicy:
    """One declared session's rotation bounds (issue #144).

    Every threshold is optional and independently declared; a policy with all three unset
    is legal and bounds nothing. A head that breaches *any* declared threshold is not
    resumed — the next member of its pool mints a fresh head instead.

    ``max_invocations`` counts **harness invocations, not node-steps**: a single node-step
    burns two or three (a spawn plus a judge, plus any nudge), so a bound set from a
    node-step count runs roughly 3x tighter than its author intends.
    """

    max_context_tokens: int | None = None
    max_transcript_bytes: int | None = None
    max_invocations: int | None = None


@dataclass(frozen=True)
class SessionDecl:
    """One graph-level named session declaration (issue #144).

    The unit ``sessions:`` declares and a node references by name (``fresh:<name>`` /
    ``resume:<name>``). It carries workflow *policy* — a capability tier, a reasoning
    effort, rotation bounds — never application knowledge (``bzh:app-agnostic-graphs``).

    ``model`` is a **prioritized preference list**, resolved left-to-right at session mint
    by the runner's harness adapter: the first entry that resolves wins, unresolvable
    entries are skipped, and an all-unresolvable list falls back to the runner's default.
    Entries are opaque preference strings to the hub — a namespaced ``blizzard:`` tier
    alias (``blizzard:frontier``/``advanced``/``basic``) or a harness-native model name.
    The hub never interprets either; the alias table lives in each runner's own config, so
    a graph stays harness-agnostic (``bzh:pluggable-seams``).

    ``effort`` is model's twin, a single value rather than a list — every adapter can map
    an ordinal *somewhere*, so there is no "unrecognized, try the next one" case. The hub
    validates it as a non-empty string only: recognizing the value (and logging an
    unrecognized one) needs the runner's config, which the hub cannot see.
    """

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
    return GraphDoc(name=name, entry=entry, nodes=nodes, sessions=_parse_sessions(raw.get("sessions")))


def _parse_sessions(raw: object) -> dict[str, SessionDecl]:
    """Parse the optional top-level ``sessions:`` map (issue #144).

    Absent reads as ``{}``. Structural coercion only — a
    session naming a node, or a ``resume:``/``fresh:`` reference naming nothing, is the
    validator's verdict, not this parser's.
    """
    if raw is None:
        return {}
    body = _as_dict(raw, "`sessions`")
    return {str(name): _parse_session(str(name), _as_dict(decl, f"session {name!r}")) for name, decl in body.items()}


def _parse_session(name: str, body: dict[str, object]) -> SessionDecl:
    raw_model = body.get("model")
    # A single string is the natural one-entry spelling (`model: blizzard:basic`) and
    # normalizes to the same one-entry list the sequence form parses to, so downstream
    # readers see exactly one shape — the same both-authored-forms normalization
    # ``_parse_produces_entry`` performs.
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

    A bare string names an asset (``kind=asset``). A mapping ``{name, kind}`` names an
    explicit kind — currently ``asset`` or ``git_commit``. Structural coercion only,
    matching :func:`_parse_node`'s other
    enum fields (``executor``, ``session``): an unrecognized ``kind`` value raises
    :class:`GraphParseError` with a clear message rather than a bare :class:`ValueError`,
    since ``produces:`` entries are user-authored graph YAML, not an internal enum."""
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
    # Whether this choice is gated on green checks (issue #114) — see
    # ``ChoiceDoc.requires_checks``.
    requires_checks: bool = False


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
    # ``None`` means "chunk most-recent" (bare ``resume``) or bare ``fresh``; a validated
    # graph never carries a malformed session, so there is no ``Node``-level malformed flag.
    session_source: str | None = None
    # Where the runner runs this node's ``checks:`` and the per-check timeout (issue #114) —
    # see ``NodeDoc.checks_cwd`` / ``NodeDoc.checks_timeout``.
    checks_cwd: str | None = None
    checks_timeout: int | None = None

    @property
    def is_hub_command_node(self) -> bool:
        """True for a generic hub command node (``executor: hub`` + a non-empty
        ``run:``) — the shape :class:`~blizzard.hub.delivery.hub_node.HubNodeExecutor`
        drives. False for every worker node. A plain predicate rather than an
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
    # The graph-level named-session declarations (issue #144), in authored order —
    # empty for every graph that declares none. A list rather than the doc's map: a
    # reified :class:`SessionDecl` already carries its own ``name``, and every reified
    # collection on this type is a list. :meth:`session_by_name` is the lookup.
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

    Keyed by ``graph_id``. Encodes the same "newest-per-name, retired excluded" rule
    :meth:`IReadGraphRepository.get_enabled_by_name` applies at lookup time — a pure
    domain function so the read-listing surface (``GET /graphs``) does not re-derive
    it at the edge (``bzh:domain-core``). ``retired_ids`` names every ``graph_id``
    whose newest lifecycle fact (issue #101) reads retired; a retired graph is never a
    candidate, so a name whose every graph is retired marks none of them effective.

    Required, keyword-only, and carries no default (issue #101 lockstep note), so a
    caller that omits it gets a ``TypeError`` rather than the pre-#101 "every graph is a
    candidate" behavior (pinned by
    tests/test_graph_domain.py::test_mark_effective_requires_retired_ids_explicitly).
    Pass ``retired_ids=frozenset()`` explicitly for the pre-#101 behavior.
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


def is_newer_mint(candidate: Graph, current: Graph) -> bool:
    """Whether ``candidate`` is a strictly newer mint than ``current``.

    The fourth place this codebase needs "which of two mints is newer", and the one that
    makes it a named rule rather than a fourth open-coded tuple comparison: the ordering
    is already owned by :func:`mark_effective`'s tie-break and
    :meth:`IReadGraphRepository.get_enabled_by_name`'s ``ORDER BY``, and it must stay in
    lockstep with both.

    ``created_at`` first, ``graph_id`` as the tie-break — ULIDs sort lexically by creation,
    so two mints sharing a ``created_at`` (a fixed clock, or two mints inside one tick)
    still order deterministically rather than by whichever the store happened to return.

    Its caller is the follow-latest policy (issue #164), which needs **strictly** newer:
    ``get_enabled_by_name`` answers with the newest *non-retired* mint, so a chunk sitting
    on a since-retired mint would otherwise be dragged **backwards** onto an older enabled
    one (pinned by
    tests/test_follow_latest_policy.py::test_the_policy_never_drags_a_chunk_backwards_onto_an_older_mint).
    """
    return (candidate.created_at, candidate.graph_id) > (current.created_at, current.graph_id)


def resolve_follow_latest(graph_policy: bool | None, *, hub_default: bool) -> bool:
    """Whether a chunk pinned to a graph follows the newest mint of its name (issue #164).

    The two-level policy, resolved in one place so no caller re-spells it: the graph's own
    tri-state wins where it is set, and ``None`` — every mint's default — inherits the
    hub-level ``follow_latest``. A pure function over two already-read values rather than a
    repository read, so the precedence is unit-testable with no store (``bzh:domain-core``).

    ``hub_default`` is keyword-only and carries no default of its own (issue #164), so a
    caller that omits the hub setting gets a ``TypeError`` rather than a silent ``True``
    (migrating a fleet that never opted in) or a silent ``False`` (never migrating at
    all) — pinned by
    tests/test_pin_hub_domain.py::test_resolve_follow_latest_requires_hub_default_explicitly
    """
    return hub_default if graph_policy is None else graph_policy


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

    def newest_definition_yaml(self, name: str) -> str | None:
        """The newest-minted graph of ``name``'s stored source YAML, or ``None`` if the
        name has never been minted (issue #146).

        Reads back the ``definition_yaml`` column the mint persists "for audit and
        re-export" — reconciliation is that re-export. Deliberately **newest-minted**,
        not newest-*enabled*: this answers "what does the store already hold for this
        name", which is what makes re-running the reconciler a no-op. Retirement is a
        separate lifecycle (issue #101) and does not change what was minted, so it is not
        consulted here; the tie-break matches :meth:`get_enabled_by_name` and
        :func:`mark_effective` so "newest" means the same thing everywhere.
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
        to :meth:`is_retired`, used by the ``GET /graphs`` listing so it derives
        ``effective`` once rather than per-row.
        """
        ...

    def follow_latest(self, graph_id: str) -> bool | None:
        """This graph's own follow-latest policy — the stored tri-state (issue #164).

        ``True``/``False`` override the hub-level setting for chunks pinned to this
        mint; ``None`` — the value for a graph with no policy fact at all, which is
        every graph by default — inherits it (:func:`resolve_follow_latest`).
        Newest-fact-wins over ``graph_policy_facts``, exactly like :meth:`is_retired`
        over the lifecycle facts.
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

        ``follow_latest=None`` is a real, recordable value (revert to inheriting the hub
        setting), not "leave unchanged" — appending it is how a graph-level override is
        cleared without deleting history. Never touches the ``graphs`` row.
        """
        ...
