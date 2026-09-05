from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src" / "blizzard"
_FOUNDATION_DIR = _SRC_DIR / "foundation"
_HUB_DIR = _SRC_DIR / "hub"
_RUNNER_DIR = _SRC_DIR / "runner"

# The rule's own implementation (`bzh:injected-clock`) — the one file allowed to read the
# wall clock directly, since it is what every ``IClock.now()`` call ultimately reaches.
_CLOCK_IMPLEMENTATION_FILE = _FOUNDATION_DIR / "clock.py"

# AST-based (D1, plan blizzard-context#clean-architecture): a literal-token grep would
# misfire on `foundation/clock.py`'s and `hub/auth/signing.py`'s docstring mentions of
# `datetime.now()`, and on `time.monotonic()` — a legitimate non-wall-clock read one token
# away from `time.time()`.
_WALL_CLOCK_CALLS: frozenset[tuple[str, str]] = frozenset(
    {
        ("datetime", "now"),
        ("datetime", "utcnow"),
        ("time", "time"),
        ("func", "now"),
    }
)


def _wall_clock_read_lines(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and (func.value.id, func.attr) in _WALL_CLOCK_CALLS
        ):
            lines.append(node.lineno)
    return lines


def test_no_direct_wall_clock_read_outside_the_clock_implementation() -> None:
    """bzh:injected-clock: every timestamp flows through an injected ``IClock`` — a direct
    ``datetime.now()``, ``datetime.utcnow()``, ``time.time()``, or ``func.now()`` read is a
    violation everywhere under ``src/blizzard`` except the clock's own implementation."""
    violations: list[str] = []
    for path in sorted(_SRC_DIR.rglob("*.py")):
        if path == _CLOCK_IMPLEMENTATION_FILE:
            continue
        for lineno in _wall_clock_read_lines(path):
            violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}")
    assert not violations, f"K — every timestamp must flow through IClock: {violations}"


# The composition-root set `bzh:dependency-injection`'s Do. declares
# (blizzard-context architecture/clean-architecture.md) — the only sites concrete wiring,
# `SystemClock()` included, may happen. One entry per root, each carrying its own
# justification; a CLI command is a short-lived process with no server loop to hand a
# wired dataclass through, so wiring inline at the top of the command body is that
# process's own composition root.
_CLOCK_COMPOSITION_ROOTS: dict[Path, str] = {
    _HUB_DIR / "app.py": "build_hosted_app — the hosted hub's composition root",
    _HUB_DIR / "composition.py": "build_services — the hub's fleet-service composition root",
    _RUNNER_DIR / "app.py": "build_hosted_app — the runner's served-app composition root",
    _RUNNER_DIR / "loop" / "build.py": "LoopWiring.context — the loop's own composition root",
    _RUNNER_DIR / "cli" / "runtime.py": "a short-lived CLI process's own composition root",
    _RUNNER_DIR / "cli" / "external_usage.py": "a short-lived CLI process's own composition root",
}


def _system_clock_construction_lines(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    lines: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "SystemClock":
            lines.append(node.lineno)
    return lines


def test_system_clock_is_constructed_only_at_a_composition_root() -> None:
    """bzh:injected-clock / bzh:dependency-injection: ``SystemClock()`` is concrete wiring
    — it may be constructed only at one of the six declared composition roots, never inside
    a collaborator it is handed down to."""
    violations: list[str] = []
    for path in sorted(_SRC_DIR.rglob("*.py")):
        if path in _CLOCK_COMPOSITION_ROOTS:
            continue
        for lineno in _system_clock_construction_lines(path):
            violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}")
    assert not violations, f"L — SystemClock() may be constructed only at a composition root: {violations}"
