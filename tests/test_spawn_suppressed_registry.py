"""The spawn-gate call-site registry fitness test (issue #49).

A Python test, not a docstring enumeration to recount by hand: a new spawn primitive
landing without the gate fails this test by name. AST-walks every function in the loop
modules that start workers for a gated-shaped call also calling the gate.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOOP = _REPO_ROOT / "src" / "blizzard" / "runner" / "loop"
_SCANNED = (_LOOP / "steps.py", _LOOP / "spawn.py", _LOOP / "judgement.py", _LOOP / "dormant.py")

#: Helpers exempt from calling the gate themselves, because every call site is inside a
#: function that does — re-derived by `test_exempt_helpers_are_reached_only_from_gated_scopes`
#: rather than taken on trust.
#:
#: `_elicit` was the old synchronous elicitation call, split by blizzard#443 into `_launch`
#: (reached only from `Judgement.run`'s own gated scope) and `_relaunch` (a lost elicitation's
#: retry, reached only from `Judgement._lost`, which takes the gate itself before ever calling
#: it — a paused runner defers the relaunch exactly as it defers a fresh one).
_ALLOWED_UNGATED = frozenset({"_launch", "_relaunch", "_wake", "resume_on_unmet_produces"})

_GATE_NAME = "suppressed"
_GATED_METHODS = frozenset({"spawn", "resume_with_message", "judge"})


def _is_harness_spawn_call(node: ast.AST) -> bool:
    """True for a call shaped ``ctx.harness.spawn(...)``, ``ctx.harness.resume_with_message(...)``,
    or ``ctx.harness.judge(...)``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _GATED_METHODS
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "harness"
    )


def _is_gate_call(node: ast.AST) -> bool:
    """True for a call to the gate — always an attribute of a `Spawner`, never a bare name."""
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == _GATE_NAME


def _calls_in_own_scope(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    """Every ``ast.Call`` in ``func``'s own body — not descending into a nested def/lambda,
    whose calls belong to *that* scope, not this one."""
    calls: list[ast.Call] = []

    def _walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Call):
                calls.append(child)
            _walk(child)

    _walk(func)
    return calls


def _functions(path: Path) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(path.read_text(), filename=str(path))
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _ungated_spawn_functions(path: Path) -> list[str]:
    violations: list[str] = []
    for node in _functions(path):
        own_calls = _calls_in_own_scope(node)
        if not any(_is_harness_spawn_call(call) for call in own_calls):
            continue
        if any(_is_gate_call(call) for call in own_calls):
            continue
        if node.name in _ALLOWED_UNGATED:
            continue
        violations.append(f"{node.name} ({path.relative_to(_REPO_ROOT)}:{node.lineno})")
    return violations


def test_every_harness_spawn_call_site_is_gated() -> None:
    violations = [name for path in _SCANNED for name in _ungated_spawn_functions(path)]
    assert not violations, (
        "function(s) call `ctx.harness.spawn`/`ctx.harness.resume_with_message` without also "
        f"calling `Spawner.{_GATE_NAME}` — a runner told to spawn no workers would spawn one "
        f"(issue #45/#46): {violations}"
    )


def test_exempt_helpers_are_reached_only_from_gated_scopes() -> None:
    """The allowlist's own premise, checked: an exempt helper may skip the gate only while
    every function that calls it takes it. A new caller that forgets fails here."""
    ungated_callers: list[str] = []
    for path in _SCANNED:
        for node in _functions(path):
            calls = _calls_in_own_scope(node)
            reaches_exempt = any(isinstance(c.func, ast.Attribute) and c.func.attr in _ALLOWED_UNGATED for c in calls)
            if reaches_exempt and node.name not in _ALLOWED_UNGATED and not any(_is_gate_call(c) for c in calls):
                ungated_callers.append(f"{node.name} ({path.relative_to(_REPO_ROOT)}:{node.lineno})")
    assert not ungated_callers, (
        f"function(s) call an allowlisted helper ({sorted(_ALLOWED_UNGATED)}) without taking the "
        f"gate themselves, so the exemption no longer holds: {ungated_callers}"
    )


def test_resume_from_rides_the_gated_spawn_funnel() -> None:
    """Node-entry resume (issue #115) threads ``resume_from`` into ``Spawner.spawn``'s
    existing, already-gated spawn call — never a new, separately-gated harness-spawn
    call site of its own (AC5)."""
    carriers: list[str] = []
    for path in _SCANNED:
        for node in _functions(path):
            for call in _calls_in_own_scope(node):
                if _is_harness_spawn_call(call) and any(kw.arg == "resume_from" for kw in call.keywords):
                    carriers.append(node.name)
    assert carriers == ["spawn"], (
        "expected `resume_from` threaded into `ctx.harness.spawn` only from within "
        f"`Spawner.spawn` (the sole gated funnel); found: {carriers}"
    )


def test_scan_is_not_vacuous() -> None:
    """Guard against either half of the scan silently matching nothing (e.g. a renamed method
    or gate drifting the AST shape out from under it) and the test above passing vacuously."""
    nodes = [n for path in _SCANNED for n in ast.walk(ast.parse(path.read_text(), filename=str(path)))]
    assert any(_is_harness_spawn_call(n) for n in nodes), "no ctx.harness.spawn/resume_with_message call site matched"
    assert any(_is_gate_call(n) for n in nodes), f"no `Spawner.{_GATE_NAME}` call site matched"
