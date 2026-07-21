"""The ``_spawn_suppressed`` call-site registry fitness test (issue #49).

Anti-regression for the gate's call-site registry (issue #45: the judgement resume was
a spawn nobody had counted; issue #46 added a fifth primitive): a Python test, not a
docstring enumeration a reviewer has to recount by hand, so a sixth spawn primitive that
lands without the gate fails this test by name instead of shipping the same bug again.

AST-walks every function defined in ``runner/loop/steps.py`` (not ``ast.walk`` alone —
that would attribute a nested function's calls to its enclosing one; see
``_calls_in_own_scope``) for a call shaped ``ctx.harness.spawn(...)``,
``ctx.harness.resume_with_message(...)``, or ``ctx.harness.judge(...)`` — the judgement
resume issue #45 was itself about — and asserts the same function's own body also
calls ``_spawn_suppressed(...)``. ``_ALLOWED_UNGATED`` is the explicit exemption list for
a deliberately-ungated call that matches the scanned shape; today it is empty — every
call site the scan finds is gated in its own function — but it exists so a future,
genuinely-deliberate exemption is a one-line, commented addition here rather than a
weakening of the scan itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_STEPS_PATH = _REPO_ROOT / "src" / "blizzard" / "runner" / "loop" / "steps.py"

#: Function names deliberately exempt from the gate, each with a reason. Empty today:
#: read this test's module docstring before adding an entry — an addition here must be
#: a considered exemption, not a patch over a real gap. (``_kill_and_park_paused``'s
#: ungated kill is not an entry because it never matches the scanned call shape — it
#: calls ``ctx.process.kill``, not ``ctx.harness.spawn``/``resume_with_message``.)
_ALLOWED_UNGATED: frozenset[str] = frozenset()

_GATE_NAME = "_spawn_suppressed"
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
    """True for a call to ``_spawn_suppressed(...)``."""
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == _GATE_NAME


def _calls_in_own_scope(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.Call]:
    """Every ``ast.Call`` in ``func``'s own body — not descending into a nested def/lambda,
    whose calls belong to *that* scope, not this one."""
    calls: list[ast.Call] = []

    def _walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # a nested scope's calls are its own, not this function's
            if isinstance(child, ast.Call):
                calls.append(child)
            _walk(child)

    _walk(func)
    return calls


def _ungated_spawn_functions(path: Path) -> list[str]:
    """Every function name in ``path`` whose own body calls a gated harness primitive
    but never calls ``_spawn_suppressed``, minus the explicit allowlist."""
    tree = ast.parse(path.read_text(), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
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
    violations = _ungated_spawn_functions(_STEPS_PATH)
    assert not violations, (
        "function(s) call `ctx.harness.spawn`/`ctx.harness.resume_with_message` without also "
        "calling `_spawn_suppressed` — a runner told to spawn no workers would spawn one "
        f"(issue #45/#46): {violations}"
    )


def test_resume_from_rides_the_gated_spawn_attempt_funnel() -> None:
    """Node-entry resume (issue #115) threads ``resume_from`` into ``_spawn_attempt``'s
    existing, already-gated ``ctx.harness.spawn(...)`` call — it must never grow a new,
    separately-gated (or ungated) harness-spawn call site of its own. AST-asserts the
    ``resume_from=`` keyword is only ever passed to a harness-spawn call from inside
    ``_spawn_attempt``: a resume spawn is provably behind the same single
    ``_spawn_suppressed`` gate as every other spawn (AC5), not a parallel path."""
    tree = ast.parse(_STEPS_PATH.read_text(), filename=str(_STEPS_PATH))
    carriers: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for call in _calls_in_own_scope(node):
            if _is_harness_spawn_call(call) and any(kw.arg == "resume_from" for kw in call.keywords):
                carriers.append(node.name)
    assert carriers == ["_spawn_attempt"], (
        "expected `resume_from` threaded into `ctx.harness.spawn` only from within "
        f"`_spawn_attempt` (the sole gated funnel); found: {carriers}"
    )


def test_scan_is_not_vacuous() -> None:
    """Guard against the scan silently matching nothing (e.g. a renamed method/gate drifting
    the AST shape out from under it) and the test above passing for the wrong reason."""
    tree = ast.parse(_STEPS_PATH.read_text(), filename=str(_STEPS_PATH))
    spawn_call_sites = [node for node in ast.walk(tree) if _is_harness_spawn_call(node)]
    assert len(spawn_call_sites) >= 1, "expected at least one ctx.harness.spawn/resume_with_message call site"
