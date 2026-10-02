"""Pure composition of the ``normal``-autonomy ask denials, and the shapes that feed it."""

from __future__ import annotations

import json

import pytest

from blizzard.runner.harness.internal.opencode_permission_compose import (
    compose_ask_denials,
    merge_overrides,
    residual_asks,
)
from blizzard.runner.harness.internal.opencode_shapes import (
    OpenCodePermissionRule as Rule,
)
from blizzard.runner.harness.internal.opencode_shapes import (
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
