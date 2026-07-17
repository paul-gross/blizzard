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
    HUB_NODE_OUTCOMES,
    RESERVED_TERMINAL,
    Executor,
    GraphDoc,
    JudgedBy,
    NodeDoc,
    RetriesExhausted,
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
    """Validate a parsed graph definition against the D-071 rules."""
    errors: list[str] = []
    warnings: list[str] = []

    node_names = {n.name for n in doc.nodes}

    _check_entry(doc, node_names, errors)
    for node in doc.nodes:
        _check_node(node, node_names, errors)

    _warn_reachability(doc, node_names, warnings)

    return ValidationResult(errors=errors, warnings=warnings)


def _check_entry(doc: GraphDoc, node_names: set[str], errors: list[str]) -> None:
    if doc.entry not in node_names:
        errors.append(f"entry `{doc.entry}` does not name an existing node")


def _check_node(node: NodeDoc, node_names: set[str], errors: list[str]) -> None:
    judgement = node.judgement

    # Judgement-kind rules keyed on executor + judged-by.
    if node.executor is Executor.RUNNER:
        if judgement is None:
            errors.append(f"node `{node.name}`: a runner node must declare a judgement")
        elif judgement.by is JudgedBy.WORKER and not judgement.prompt:
            errors.append(f"node `{node.name}`: a worker-judged node must declare `judgement.prompt`")
        elif judgement.by is JudgedBy.HUMAN and judgement.prompt:
            errors.append(f"node `{node.name}`: a human-judged (gate) node must not declare `judgement.prompt`")
    elif node.executor is Executor.HUB and judgement is not None:
        known = HUB_NODE_OUTCOMES.get(node.name)
        if known is None:
            errors.append(f"hub node `{node.name}`: no known machinery outcome set to override")
        else:
            for choice in judgement.choices:
                if choice.name not in known:
                    allowed = ", ".join(sorted(known))
                    errors.append(
                        f"hub node `{node.name}`: choice `{choice.name}` is not a known outcome (one of: {allowed})"
                    )

    # Every choice entry has a description and a `to` that resolves.
    if judgement is not None:
        for choice in judgement.choices:
            if not choice.description:
                errors.append(f"node `{node.name}` choice `{choice.name}`: missing `description`")
            if choice.to is None:
                errors.append(f"node `{node.name}` choice `{choice.name}`: missing `to`")
            elif choice.to != RESERVED_TERMINAL and choice.to not in node_names:
                errors.append(
                    f"node `{node.name}` choice `{choice.name}`: `to: {choice.to}` resolves to no node "
                    f"(and is not the reserved terminal `{RESERVED_TERMINAL}`)"
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
    """Adjacency including hub nodes' machinery-default outcomes.

    A hub node may omit its judgement to accept the machinery defaults (deliver's
    ``landed -> done`` and ``conflict -> entry``); those are real edges for
    reachability even though the authoring doc never spells them out. Authored
    choices override the matching default.
    """
    out: dict[str, set[str]] = {n.name: set() for n in doc.nodes}
    for node in doc.nodes:
        authored: set[str] = set()
        if node.judgement is not None:
            for choice in node.judgement.choices:
                authored.add(choice.name)
                if choice.to is not None:
                    out[node.name].add(choice.to)
        if node.executor is Executor.HUB and node.name in HUB_NODE_OUTCOMES:
            for outcome in HUB_NODE_OUTCOMES[node.name]:
                if outcome in authored:
                    continue  # an authored choice already routes this outcome
                out[node.name].add(_default_hub_target(outcome, doc.entry))
    return out


def _default_hub_target(outcome: str, entry: str) -> str:
    """The default routing for a hub node's machinery outcome."""
    return RESERVED_TERMINAL if outcome == "landed" else entry


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
