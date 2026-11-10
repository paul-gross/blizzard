"""Delivery dispatches parked policies through the registry, not concrete policy names."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_DELIVERY = Path(__file__).parents[2] / "src/blizzard/hub/delivery"
_OWNERS = {Path("actions/landing/policy.py"), Path("actions/landing/park.py")}


def _violations(path: Path, source: str) -> list[str]:
    tree = ast.parse(source)
    policies = {"LandingPolicy"}
    parks = {"Park"}
    modules: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    modules[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if node.module == "blizzard.foundation.landing_policy" and alias.name == "LandingPolicy":
                    policies.add(alias.asname or alias.name)
                if node.module == "blizzard.hub.delivery.actions.landing.park" and alias.name == "Park":
                    parks.add(alias.asname or alias.name)

    def qualified(node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return modules.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return f"{qualified(node.value)}.{node.attr}"
        return ""

    failures: list[str] = []
    for node in ast.walk(tree):
        if path not in _OWNERS:
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "PARK"
                and (
                    qualified(node.value) in policies
                    or qualified(node.value) == "blizzard.foundation.landing_policy.LandingPolicy"
                )
            ):
                failures.append(f"{path}:{node.lineno} LandingPolicy.PARK")
            if isinstance(node, ast.Call) and (
                qualified(node.func) in parks
                or qualified(node.func) == "blizzard.hub.delivery.actions.landing.park.Park"
            ):
                failures.append(f"{path}:{node.lineno} Park()")
        if path == Path("flow_turn.py") and isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            if any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops) and any(
                isinstance(operand, ast.Attribute)
                and operand.attr == "name"
                and isinstance(operand.value, ast.Name)
                and operand.value.id == "spec"
                for operand in operands
            ):
                failures.append(f"{path}:{node.lineno} spec.name comparison")
    return sorted(failures)


def test_delivery_has_no_policy_specific_dispatch() -> None:
    violations = [
        violation
        for file in sorted(_DELIVERY.rglob("*.py"))
        for violation in _violations(file.relative_to(_DELIVERY), file.read_text())
    ]
    assert violations == []


@pytest.mark.parametrize(
    ("path", "source", "expected"),
    [
        ("actions/land.py", "held = LandingPolicy.PARK", "LandingPolicy.PARK"),
        ("actions/land.py", "watcher = Park()", "Park()"),
        (
            "actions/land.py",
            "from blizzard.foundation.landing_policy import LandingPolicy as LP\nheld = LP.PARK",
            "LandingPolicy.PARK",
        ),
        (
            "actions/land.py",
            "from blizzard.hub.delivery.actions.landing.park import Park as Watch\nwatcher = Watch()",
            "Park()",
        ),
        ("flow_turn.py", 'watch = spec.name == "land"', "spec.name comparison"),
        ("flow_turn.py", 'watch = "land" != spec.name', "spec.name comparison"),
    ],
)
def test_planted_concrete_dispatch_is_caught(path: str, source: str, expected: str) -> None:
    assert len(found := _violations(Path(path), source)) == 1
    assert found[0].endswith(expected)


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("actions/landing/policy.py", "policies = {LandingPolicy.PARK: Park()}"),
        ("actions/landing/park.py", "policy = LandingPolicy.PARK\nwatcher = Park()"),
        ("actions/land.py", 'note = "LandingPolicy.PARK and Park()"\n# Park()'),
        ("actions/land.py", "watcher = watchers[policy]"),
        ("flow_turn.py", "watch = spec.lands"),
        ("flow_turn.py", 'name = spec.name\nresult = node.name == "land"'),
    ],
)
def test_permitted_sources_are_not_caught(path: str, source: str) -> None:
    assert _violations(Path(path), source) == []
