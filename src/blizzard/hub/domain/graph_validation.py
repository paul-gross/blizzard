"""Mint-time graph validation.

The rules ``POST /graphs`` runs before minting a graph immutable. Errors
reject the definition; warnings mint it flagged. This is pure domain logic over an
already-parsed :class:`GraphDoc` (``bzh:domain-core``): file-reference resolution
and YAML parsing are edge concerns done before it runs, so the validator touches
no filesystem and no framework.

The fused choice/edge shape makes "every choice has an edge" *structurally*
unrepresentable — a choice entry carries its own ``to``. What remains to check is
that each ``to`` resolves, the entry node exists, judgement kind matches executor,
and the retry escape hatch is well-formed. Reachability is a warning, not an error:
cycles are intentional and retries escape every cycle to escalation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from blizzard.hub.domain.graph import (
    RESERVED_TERMINAL,
    SESSION_LEGAL_FORMS,
    Executor,
    GraphDoc,
    JudgedBy,
    NodeDoc,
    RetriesExhausted,
    SessionDecl,
    SessionMode,
    classify_choice_target,
)


@dataclass(frozen=True)
class ValidationResult:
    """The outcome of validating a graph definition."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when no error rejects the definition (warnings still mint)."""
        return not self.errors


def validate_graph(doc: GraphDoc) -> ValidationResult:
    """Validate a parsed graph definition against its structural rules."""
    errors: list[str] = []
    warnings: list[str] = []

    node_names = {n.name for n in doc.nodes}
    session_names = set(doc.sessions)

    _check_entry(doc, node_names, errors)
    _check_sessions(doc, node_names, errors)
    for node in doc.nodes:
        _check_node(node, node_names, session_names, errors)

    _warn_reachability(doc, node_names, warnings)

    return ValidationResult(errors=errors, warnings=warnings)


def _check_sessions(doc: GraphDoc, node_names: set[str], errors: list[str]) -> None:
    """The graph-level ``sessions:`` map's own rules (issue #144).

    A session name and a node name share one reference namespace — ``resume:<name>``
    resolves declared-session-first, node-second — so a collision would make a node's
    own lineage silently unreachable. Reject it rather than pick a winner.

    ``effort`` is checked as a **non-empty string only**: the vocabulary is
    ``low|medium|high|max`` plus whatever a runner's ``[effort.aliases]`` adds, and the
    hub cannot see runner config. Recognizing the value — and logging an unrecognized
    one — is the adapter's job (``bzh:one-owner``).
    """
    for name, decl in doc.sessions.items():
        if name in node_names:
            errors.append(
                f"session `{name}`: a session name may not collide with a node name — "
                f"`resume:{name}` would resolve to the session and never the node"
            )
        _check_session_decl(name, decl, errors)


def _check_session_decl(name: str, decl: SessionDecl, errors: list[str]) -> None:
    for entry in decl.model:
        if not entry:
            errors.append(f"session `{name}`: `model` entries must be non-empty strings")
    if decl.effort is not None and not decl.effort.strip():
        errors.append(f"session `{name}`: `effort` must be a non-empty string")
    rotate = decl.rotate
    if rotate is None:
        return
    for field_name, value in (
        ("max_context_tokens", rotate.max_context_tokens),
        ("max_transcript_bytes", rotate.max_transcript_bytes),
        ("max_invocations", rotate.max_invocations),
    ):
        if value is not None and value <= 0:
            errors.append(f"session `{name}`: `rotate.{field_name}` must be a positive number")


def _check_entry(doc: GraphDoc, node_names: set[str], errors: list[str]) -> None:
    if doc.entry not in node_names:
        errors.append(f"entry `{doc.entry}` does not name an existing node")


def _check_node(node: NodeDoc, node_names: set[str], session_names: set[str], errors: list[str]) -> None:
    judgement = node.judgement

    # `run:` is legal ONLY on a hub node — the generic hub command node (#65). Reject it
    # on a runner (worker) node, where it is meaningless: a worker node's step is an
    # agent turn, not a declared command list.
    if node.run and node.executor is not Executor.HUB:
        errors.append(f"node `{node.name}`: `run:` is only legal on a hub node (`executor: hub`)")

    # The pending-poll cadence (#66) is legal only on a generic hub command node — a
    # node with no `run:` step ever reports `pending`, so the fields are meaningless
    # anywhere else.
    is_hub_command_node = node.executor is Executor.HUB and bool(node.run)
    if (node.poll_interval_seconds is not None or node.poll_timeout_seconds is not None) and not is_hub_command_node:
        errors.append(
            f"node `{node.name}`: `poll_interval`/`poll_timeout` are only legal on a "
            f"hub command node (`executor: hub` with `run:`)"
        )
    if node.poll_interval_seconds is not None and node.poll_interval_seconds <= 0:
        errors.append(f"node `{node.name}`: `poll_interval` must be a positive number of seconds")
    if node.poll_timeout_seconds is not None and node.poll_timeout_seconds <= 0:
        errors.append(f"node `{node.name}`: `poll_timeout` must be a positive number of seconds")
    if (
        node.poll_interval_seconds is not None
        and node.poll_timeout_seconds is not None
        and node.poll_timeout_seconds < node.poll_interval_seconds
    ):
        errors.append(f"node `{node.name}`: `poll_timeout` must be >= `poll_interval`")

    # Judgement-kind rules keyed on executor + judged-by.
    if node.executor is Executor.RUNNER:
        if judgement is None:
            errors.append(f"node `{node.name}`: a runner node must declare a judgement")
        elif judgement.by is JudgedBy.WORKER and not judgement.prompt:
            errors.append(f"node `{node.name}`: a worker-judged node must declare `judgement.prompt`")
        elif judgement.by is JudgedBy.HUMAN and judgement.prompt:
            errors.append(f"node `{node.name}`: a human-judged (gate) node must not declare `judgement.prompt`")
    elif node.executor is Executor.HUB:
        # The generic hub command node (#65, #67) — structurally agentless: no prompt,
        # no worker judgement prose, no in-session checks. Its choices are authored like
        # a worker node's own (a fused choice/edge per outcome its commands can emit),
        # checked generically below like every other node's choices — no node name is
        # privileged by the engine, and no choice is restricted from routing straight
        # to the reserved terminal.
        if node.prompt is not None:
            errors.append(f"hub node `{node.name}`: a hub command node must not declare `prompt`")
        if node.checks:
            errors.append(f"hub node `{node.name}`: a hub command node must not declare `checks`")
        if judgement is not None and judgement.prompt:
            errors.append(f"hub node `{node.name}`: a hub command node must not declare `judgement.prompt`")
        if judgement is None:
            errors.append(
                f"hub node `{node.name}`: a hub command node must declare a judgement "
                f"(its outcome choices — at least the edges its commands route)"
            )

    # Checks gating (issue #114). `checks_cwd`/`checks_timeout` configure how a node's
    # `checks:` run, and a choice's `requires_checks` gates routing on them — all three
    # are meaningless without a `checks:` list to run, so reject them on a node that
    # declares none. (A hub node can never declare `checks:` — rejected just above — so
    # this also fences `requires_checks`/`checks_cwd`/`checks_timeout` off hub nodes.)
    if not node.checks:
        if node.checks_cwd is not None:
            errors.append(f"node `{node.name}`: `checks_cwd` is only legal on a node that declares `checks:`")
        if node.checks_timeout is not None:
            errors.append(f"node `{node.name}`: `checks_timeout` is only legal on a node that declares `checks:`")
    if node.checks_timeout is not None and node.checks_timeout <= 0:
        errors.append(f"node `{node.name}`: `checks_timeout` must be a positive number of seconds")
    if judgement is not None:
        for choice in judgement.choices:
            if not choice.requires_checks:
                continue
            if not node.checks:
                errors.append(
                    f"node `{node.name}` choice `{choice.name}`: `requires_checks` is only legal on a "
                    f"choice whose node declares `checks:`"
                )
            # A human-judged (gate) node's exit is a person's signoff, not a worker
            # judging against mechanical checks — the runner-local checks gate never runs
            # there, so `requires_checks` would be inert and misleading. Reject it.
            if judgement.by is JudgedBy.HUMAN:
                errors.append(
                    f"node `{node.name}` choice `{choice.name}`: `requires_checks` is not legal on a "
                    f"human-judged (gate) node"
                )

    # Every choice entry has a description and a `to` that resolves. A `to` is one of:
    # a same-graph node name, the reserved terminal, or a well-formed cross-graph
    # `graph:<name>` target (issue #90) — a malformed `graph:` form is rejected here.
    if judgement is not None:
        for choice in judgement.choices:
            if not choice.description:
                errors.append(f"node `{node.name}` choice `{choice.name}`: missing `description`")
            if choice.to is None:
                errors.append(f"node `{node.name}` choice `{choice.name}`: missing `to`")
                continue
            kind, _ = classify_choice_target(choice.to)
            if kind == "malformed":
                errors.append(
                    f"node `{node.name}` choice `{choice.name}`: malformed cross-graph target "
                    f"`to: {choice.to}` — expected `graph:<name>`"
                )
            elif kind == "node" and choice.to != RESERVED_TERMINAL and choice.to not in node_names:
                errors.append(
                    f"node `{node.name}` choice `{choice.name}`: `to: {choice.to}` resolves to no node "
                    f"(and is not the reserved terminal `{RESERVED_TERMINAL}`)"
                )

    # A node's `session:` value (issues #115, #144) — the targeted analogue of a choice
    # `to` above: a malformed form is rejected, and a well-formed reference must resolve.
    #
    # `resume:<name>` resolves declared-session-first, node-second (#144 keeps #115's
    # `resume:<node>` working by leaving node names as the second tier). `fresh:<name>`
    # resolves against declared sessions *only* (D1): `fresh` always mints, so a node name
    # there would name nothing — a session minted at node Y is not in node X's implicit
    # lineage. It is a validation error, not a silently-inert reference.
    if node.session_malformed:
        errors.append(f"node `{node.name}`: malformed session value — expected {SESSION_LEGAL_FORMS}")
    elif node.session_source is None:
        pass
    elif node.session is SessionMode.FRESH:
        if node.session_source not in session_names:
            errors.append(
                f"node `{node.name}`: session `fresh:{node.session_source}` names no declared session "
                f"(`fresh:<name>` must name a `sessions:` entry, never a node)"
            )
    elif node.session_source not in session_names and node.session_source not in node_names:
        errors.append(
            f"node `{node.name}`: session `resume:{node.session_source}` names neither a declared session nor a node"
        )

    # The retry escape hatch, when present, is the only legal exhaustion target.
    if node.retries_exhausted is not None and node.retries_exhausted != RetriesExhausted.ESCALATE.value:
        errors.append(
            f"node `{node.name}`: retries.exhausted must be `{RetriesExhausted.ESCALATE.value}`, "
            f"got `{node.retries_exhausted}`"
        )


def _warn_reachability(doc: GraphDoc, node_names: set[str], warnings: list[str]) -> None:
    if doc.entry not in node_names:
        return  # an entry error already fired; reachability is meaningless
    edges = _edges(doc)
    reachable = _reachable_from(doc.entry, edges)

    for name in sorted(node_names - reachable):
        warnings.append(f"node `{name}` is unreachable from entry `{doc.entry}`")

    if RESERVED_TERMINAL not in _reachable_from(doc.entry, edges, include_terminal=True):
        warnings.append(f"no path from entry `{doc.entry}` to the terminal `{RESERVED_TERMINAL}`")


def _edges(doc: GraphDoc) -> dict[str, set[str]]:
    """Adjacency built from every node's authored choices — including hub nodes.

    No node name is privileged (#67): a hub command node's choices are authored like
    a worker node's own (a fused choice/edge per outcome its commands can emit), so
    this reads the same ``judgement.choices`` for every node.
    """
    out: dict[str, set[str]] = {n.name: set() for n in doc.nodes}
    for node in doc.nodes:
        if node.judgement is not None:
            for choice in node.judgement.choices:
                # A cross-graph target (issue #90) is an exit *out* of this graph, not an
                # intra-graph edge — like the terminal, it never contributes adjacency to
                # a sibling node, so reachability is computed over same-graph targets only.
                if choice.to is not None and classify_choice_target(choice.to)[0] == "node":
                    out[node.name].add(choice.to)
    return out


def _reachable_from(start: str, edges: dict[str, set[str]], *, include_terminal: bool = False) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for target in edges.get(current, set()):
            if target == RESERVED_TERMINAL and not include_terminal:
                continue
            stack.append(target)
    return seen
