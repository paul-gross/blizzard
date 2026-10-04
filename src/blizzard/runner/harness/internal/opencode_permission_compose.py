"""Pure composition of the ``deny`` overrides that keep an unattended OpenCode worker off its ``ask`` path.

An unanswered ask is auto-rejected and the rejection stops the agent loop; a ``deny`` returns a tool error and the
loop continues. So in ``normal`` autonomy every reachable ``ask`` becomes ``deny`` in the layer it came from, at the
same key, which keeps the operator's later-match-wins order. No I/O."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from blizzard.foundation.roles import dto
from blizzard.runner.harness.internal.opencode_shapes import OpenCodePermissionRule

_ASK = "ask"
_DENY = "deny"


@dto
@dataclass(frozen=True)
class ResidualAsk:
    """An ``ask`` rule that can still decide a request for ``agent``."""

    agent: str
    permission: str
    pattern: str

    def describe(self) -> str:
        return f"agent {self.agent!r} permission {self.permission!r} pattern {self.pattern!r}"


def _unshadowed_asks(rules: tuple[OpenCodePermissionRule, ...]) -> list[int]:
    """Indices of ``ask`` rules no later rule of the same permission, with the same or ``*`` pattern, overrides.
    Conservative: a narrower later rule never counts as shadowing."""
    found: list[int] = []
    for index, rule in enumerate(rules):
        if rule.action != _ASK:
            continue
        shadowed = any(
            later.permission == rule.permission and later.pattern in (rule.pattern, "*") for later in rules[index + 1 :]
        )
        if not shadowed:
            found.append(index)
    return found


def residual_asks(agent_rulesets: Mapping[str, tuple[OpenCodePermissionRule, ...]]) -> tuple[ResidualAsk, ...]:
    """Every ``ask`` that can still resolve, across every agent."""
    return tuple(
        ResidualAsk(agent, rules[index].permission, rules[index].pattern)
        for agent, rules in sorted(agent_rulesets.items())
        for index in _unshadowed_asks(rules)
    )


def _holds_ask(permission_node: object, permission: str, pattern: str) -> bool:
    """Whether a config ``permission`` object itself writes this ``ask``."""
    if not isinstance(permission_node, dict):
        return False
    entry = permission_node.get(permission)
    if isinstance(entry, str):
        return pattern == "*" and entry == _ASK
    return isinstance(entry, dict) and entry.get(pattern) == _ASK


def _is_map(permission_node: object, permission: str) -> bool:
    return isinstance(permission_node, dict) and isinstance(permission_node.get(permission), dict)


def _deny_in(target: dict[str, Any], permission: str, pattern: str, *, as_map: bool) -> None:
    """Write the deny in the shape the layer wrote the ask: a scalar for a bare ``*``, else a pattern map."""
    existing = target.get(permission)
    if pattern == "*" and not as_map and not isinstance(existing, dict):
        target[permission] = _DENY
        return
    if not isinstance(existing, dict):
        existing = {} if existing is None else {"*": existing}
        target[permission] = existing
    existing[pattern] = _DENY


def compose_ask_denials(
    merged_config: Mapping[str, Any], agent_rulesets: Mapping[str, tuple[OpenCodePermissionRule, ...]]
) -> dict[str, Any]:
    """The override document (empty when nothing can ask) that rewrites every reachable ``ask`` to ``deny``.

    An ask the merged config writes is denied in place — top-level ``permission`` or ``agent.<name>.permission``,
    wherever it was written. A built-in default ask has no config position, so it is denied in that agent's own
    layer, and the same permission's later default rules are restated after it so their outcome survives."""
    top = merged_config.get("permission")
    agents = merged_config.get("agent")
    top_overrides: dict[str, Any] = {}
    agent_overrides: dict[str, dict[str, Any]] = {}
    for agent, rules in sorted(agent_rulesets.items()):
        agent_node = agents.get(agent) if isinstance(agents, dict) else None
        agent_permission = agent_node.get("permission") if isinstance(agent_node, dict) else None
        for index in _unshadowed_asks(rules):
            rule = rules[index]
            if _holds_ask(agent_permission, rule.permission, rule.pattern):
                _deny_in(
                    agent_overrides.setdefault(agent, {}),
                    rule.permission,
                    rule.pattern,
                    as_map=_is_map(agent_permission, rule.permission),
                )
            elif _holds_ask(top, rule.permission, rule.pattern):
                _deny_in(top_overrides, rule.permission, rule.pattern, as_map=_is_map(top, rule.permission))
            else:
                target = agent_overrides.setdefault(agent, {})
                later_rules = [later for later in rules[index + 1 :] if later.permission == rule.permission]
                # Some permissions (`doom_loop`) take only a bare action; a map is for ones with patterns to keep.
                _deny_in(target, rule.permission, rule.pattern, as_map=bool(later_rules))
                restated = target[rule.permission]
                for later in later_rules:
                    if isinstance(restated, dict) and restated.get(later.pattern) != _DENY:
                        restated[later.pattern] = later.action
    document: dict[str, Any] = {}
    if top_overrides:
        document["permission"] = top_overrides
    if agent_overrides:
        document["agent"] = {name: {"permission": permission} for name, permission in agent_overrides.items()}
    return document


def merge_overrides(base: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    """``overrides`` deep-merged into ``base`` the way OpenCode merges config layers: objects recurse, anything
    else replaces, and an existing key keeps its position."""
    merged = dict(base)
    for key, value in overrides.items():
        current = merged.get(key)
        if isinstance(value, Mapping) and isinstance(current, dict):
            merged[key] = merge_overrides(current, value)
        elif isinstance(value, Mapping):
            merged[key] = merge_overrides({}, value)
        else:
            merged[key] = value
    return merged


__all__ = ["ResidualAsk", "compose_ask_denials", "merge_overrides", "residual_asks"]
