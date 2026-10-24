"""The carry-over of a stored ``run:`` onto the action its command already named.

:func:`translated` reads a pre-action document as what it already was — a hub node whose one
step ran a packaged script names that script's action, one with no step names ``pass`` — so
reconciling it against the packaged graph finds no change. A node it cannot carry keeps its
``run:``, and the document still parses."""

from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from dataclasses import replace
from types import MappingProxyType

from blizzard.hub.domain.graph.actions import GARDEN_DELIVER, LAND, PASS, RECORD_FINDINGS
from blizzard.hub.domain.graph.model import GraphDoc, NodeDoc, RunStepDoc

#: Each packaged script a stored step ran, by module path, and the action it was.
LEGACY_RUN_COMMANDS: Mapping[str, str] = MappingProxyType(
    {
        "blizzard.hub.graphs.scripts.land_pr_ci": LAND.name,
        "blizzard.hub.graphs.scripts.land_default": LAND.name,
        "blizzard.hub.graphs.scripts.land_ff": LAND.name,
        "blizzard.hub.graphs.scripts.review_deliver": RECORD_FINDINGS.name,
        "blizzard.hub.graphs.scripts.garden_deliver": GARDEN_DELIVER.name,
    }
)

_INTERPRETERS = frozenset({"python3", "python"})

# Each action's command flags, by the ``with:`` argument they name; a repeated flag's values join a list.
_ACTION_FLAGS: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {GARDEN_DELIVER.name: MappingProxyType({"--delta": "delta", "--proposals": "proposals"})}
)


def translated(doc: GraphDoc) -> GraphDoc:
    """``doc`` with every hub node that names no action and whose ``run:`` translates carried
    onto its action; every other node as parsed."""
    return replace(doc, nodes=[_carried(node) for node in doc.nodes])


def legacy_action(run: Sequence[RunStepDoc]) -> tuple[str, dict[str, tuple[str, ...]]] | None:
    """The action and ``with:`` arguments ``run`` stood for, or ``None`` when it stood for none."""
    if not run:
        return PASS.name, {}
    if len(run) != 1:
        return None
    try:
        argv = shlex.split(run[0].command)
    except ValueError:
        return None
    if len(argv) < 3 or argv[0] not in _INTERPRETERS or argv[1] != "-m":
        return None
    action = LEGACY_RUN_COMMANDS.get(argv[2])
    if action is None:
        return None
    args = _flag_args(argv[3:], _ACTION_FLAGS.get(action, {}))
    return None if args is None else (action, args)


def _carried(node: NodeDoc) -> NodeDoc:
    if not node.is_hub_command_node or node.action is not None:
        return node
    carried = legacy_action(node.run)
    if carried is None:
        return node
    action, args = carried
    return replace(node, run=[], names_run=False, action=action, action_args=args)


def _flag_args(argv: Sequence[str], flags: Mapping[str, str]) -> dict[str, tuple[str, ...]] | None:
    """``argv`` read as ``flag value`` (or ``flag=value``) pairs over ``flags``, or ``None`` when
    it carries anything else."""
    args: dict[str, list[str]] = {}
    rest = list(argv)
    while rest:
        flag, _, value = rest.pop(0).partition("=")
        if flag not in flags:
            return None
        if not value:
            if not rest:
                return None
            value = rest.pop(0)
        args.setdefault(flags[flag], []).append(value)
    return {key: tuple(values) for key, values in args.items()}
