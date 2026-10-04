"""Pure composition of the ``normal``-autonomy ask denials, and the shapes that feed it."""

from __future__ import annotations

import json

import pytest

from blizzard.runner.harness.opencode.permissions.permission_compose import (
    _deny_in,
    _holds_ask,
    compose_ask_denials,
    merge_overrides,
    residual_asks,
)
from blizzard.runner.harness.opencode.shapes import (
    OpenCodePermissionRule as Rule,
)
from blizzard.runner.harness.opencode.shapes import (
    OpenCodeShapeError,
    parse_agent_rulesets,
    parse_resolved_config,
)

pytestmark = pytest.mark.unit


def test_a_top_level_scalar_ask_is_denied_in_place() -> None:
    config = {"permission": {"bash": "ask"}}
    rules = {"build": (Rule("*", "*", "allow"), Rule("bash", "*", "ask"))}

    assert compose_ask_denials(config, rules) == {"permission": {"bash": "deny"}}


def test_a_pattern_map_ask_denies_only_that_pattern() -> None:
    config = {"permission": {"bash": {"*": "ask", "ls": "allow"}}}
    rules = {"build": (Rule("bash", "*", "ask"), Rule("bash", "ls", "allow"))}

    assert compose_ask_denials(config, rules) == {"permission": {"bash": {"*": "deny"}}}


def test_an_agent_level_ask_is_denied_in_that_agents_layer() -> None:
    config = {"agent": {"build": {"permission": {"edit": "ask"}}}}
    rules = {"build": (Rule("edit", "*", "ask"),), "plan": (Rule("edit", "*", "deny"),)}

    assert compose_ask_denials(config, rules) == {"agent": {"build": {"permission": {"edit": "deny"}}}}


def test_a_default_ask_is_denied_per_agent_and_restates_the_later_default_rules() -> None:
    rules = {
        "build": (
            Rule("external_directory", "*", "ask"),
            Rule("external_directory", "/tmp/opencode/*", "allow"),
            Rule("read", "*", "allow"),
        )
    }

    assert compose_ask_denials({}, rules) == {
        "agent": {
            "build": {"permission": {"external_directory": {"*": "deny", "/tmp/opencode/*": "allow"}}},
        }
    }


def test_non_ask_and_shadowed_rules_are_untouched() -> None:
    rules = {"build": (Rule("bash", "*", "ask"), Rule("bash", "*", "allow"), Rule("edit", "*", "deny"))}

    assert compose_ask_denials({"permission": {"bash": "ask"}}, rules) == {}
    assert residual_asks(rules) == ()


def test_a_narrower_later_rule_does_not_shadow_an_ask() -> None:
    rules = {"build": (Rule("read", "*.env", "ask"), Rule("read", "a.env", "allow"))}

    assert [(r.agent, r.permission, r.pattern) for r in residual_asks(rules)] == [("build", "read", "*.env")]


def test_the_composed_override_merges_in_place_and_clears_the_residual() -> None:
    base = {"permission": {"question": "deny", "bash": "ask", "edit": "allow"}}
    overrides = compose_ask_denials(base, {"build": (Rule("bash", "*", "ask"),)})

    merged = merge_overrides(base, overrides)

    assert list(merged["permission"]) == ["question", "bash", "edit"]
    assert merged["permission"]["bash"] == "deny"
    assert base["permission"]["bash"] == "ask"


def test_a_scalar_is_replaced_by_an_override_object() -> None:
    assert merge_overrides({"permission": {"read": "allow"}}, {"permission": {"read": {"*.env": "deny"}}}) == {
        "permission": {"read": {"*.env": "deny"}}
    }


def test_agent_list_output_parses_into_per_agent_rulesets() -> None:
    body = json.dumps([{"permission": "bash", "pattern": "*", "action": "ask"}], indent=2).replace("\n", "\n  ")
    text = f"build (primary)\n  {body}\nexplore (subagent)\n  []\n"

    parsed = parse_agent_rulesets(text)

    assert parsed == {"build": (Rule("bash", "*", "ask"),), "explore": ()}


@pytest.mark.parametrize(
    "text",
    ["", "build (primary)\n  not json", 'build (primary)\n  [{"permission":"a","pattern":"*","action":"maybe"}]'],
)
def test_a_malformed_agent_list_raises_a_shape_error(text: str) -> None:
    with pytest.raises(OpenCodeShapeError):
        parse_agent_rulesets(text)


@pytest.mark.parametrize("text", ["nope", "[]"])
def test_a_malformed_resolved_config_raises_a_shape_error(text: str) -> None:
    with pytest.raises(OpenCodeShapeError):
        parse_resolved_config(text)


def test_a_deny_promotes_an_existing_scalar_to_a_wildcard_map() -> None:
    target: dict[str, object] = {"read": "allow"}

    _deny_in(target, "read", "*.env", as_map=False)

    assert target == {"read": {"*": "allow", "*.env": "deny"}}


@pytest.mark.parametrize(
    ("target", "pattern", "as_map", "expected"),
    [
        ({}, "*", False, {"bash": "deny"}),
        ({}, "*", True, {"bash": {"*": "deny"}}),
        ({}, "ls", False, {"bash": {"ls": "deny"}}),
        ({"bash": {"*": "allow"}}, "*", False, {"bash": {"*": "deny"}}),
        ({"bash": "allow"}, "*", False, {"bash": "deny"}),
    ],
)
def test_a_deny_is_written_in_the_shape_the_ask_was(
    target: dict[str, object], pattern: str, as_map: bool, expected: dict[str, object]
) -> None:
    _deny_in(target, "bash", pattern, as_map=as_map)

    assert target == expected


@pytest.mark.parametrize(
    ("node", "permission", "pattern", "held"),
    [
        ({"bash": "ask"}, "bash", "*", True),
        ({"bash": "ask"}, "bash", "ls", False),
        ({"bash": "allow"}, "bash", "*", False),
        ({"bash": {"ls": "ask"}}, "bash", "ls", True),
        ({"bash": {"ls": "allow"}}, "bash", "ls", False),
        ({"bash": {"ls": "ask"}}, "bash", "*", False),
        ({"bash": 3}, "bash", "*", False),
        ({}, "bash", "*", False),
        (None, "bash", "*", False),
    ],
)
def test_holds_ask_is_true_only_for_the_exact_ask(node: object, permission: str, pattern: str, held: bool) -> None:
    assert _holds_ask(node, permission, pattern) is held


def test_an_agent_level_scalar_ask_is_denied_as_a_scalar_and_a_map_ask_as_a_map() -> None:
    config = {
        "agent": {
            "build": {"permission": {"edit": "ask"}},
            "plan": {"permission": {"bash": {"*": "allow", "rm": "ask"}}},
        }
    }
    rules = {
        "build": (Rule("edit", "*", "ask"),),
        "plan": (Rule("bash", "*", "allow"), Rule("bash", "rm", "ask")),
    }

    assert compose_ask_denials(config, rules) == {
        "agent": {"build": {"permission": {"edit": "deny"}}, "plan": {"permission": {"bash": {"rm": "deny"}}}}
    }


def test_an_agent_layer_ask_wins_over_the_same_ask_at_the_top_level() -> None:
    config = {"permission": {"edit": "ask"}, "agent": {"build": {"permission": {"edit": "ask"}}}}

    assert compose_ask_denials(config, {"build": (Rule("edit", "*", "ask"),)}) == {
        "agent": {"build": {"permission": {"edit": "deny"}}}
    }


def test_an_ask_written_by_neither_layer_is_denied_in_the_agent_layer_even_with_a_top_level_scalar() -> None:
    config = {"permission": {"read": "allow"}, "agent": {"build": {"permission": "bad"}, "plan": "bad"}}
    rules = {"build": (Rule("read", "*.env", "ask"),), "plan": (Rule("read", "*.env", "ask"),)}

    assert compose_ask_denials(config, rules) == {
        "agent": {
            "build": {"permission": {"read": {"*.env": "deny"}}},
            "plan": {"permission": {"read": {"*.env": "deny"}}},
        }
    }
