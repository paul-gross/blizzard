from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.support.repo_files import repo_root

_REPO_ROOT = repo_root()
_SRC_DIR = _REPO_ROOT / "src" / "blizzard"
_FOUNDATION_DIR = _SRC_DIR / "foundation"
_HUB_DIR = _SRC_DIR / "hub"
_RUNNER_DIR = _SRC_DIR / "runner"

# The rule's own implementation (`bzh:injected-clock`) — the one file allowed to read the
# system clocks directly, since it is what every ``IClock.now()`` and ``IMonotonicClock`` call
# ultimately reaches.
_CLOCK_IMPLEMENTATION_FILE = _FOUNDATION_DIR / "clock.py"

# The hand-run OpenCode compatibility tooling, reached only through the `opencode compatibility`
# CLI command: a probe that runs once under an operator's eye, with no server loop, lease, or test
# clock to hand a seam down from. Matched by package segment so both harness generations are covered.
_COMPATIBILITY_PACKAGE = "compatibility"

# AST-based (plan blizzard-context#clean-architecture): a literal-token grep would
# misfire on `src/blizzard/foundation/clock.py`'s and `src/blizzard/hub/auth/signing.py`'s docstring mentions of
# `datetime.now()`.
_WALL_CLOCK_CALLS: frozenset[tuple[str, str]] = frozenset(
    {
        ("datetime", "now"),
        ("datetime", "utcnow"),
        ("func", "now"),
    }
)
_WALL_CLOCK_TIME_FUNCTIONS: frozenset[str] = frozenset({"time", "time_ns"})

# The mechanical-deadline reads `IMonotonicClock` carries: ambient, they cannot be advanced by a test.
_AMBIENT_TIME_FUNCTIONS: frozenset[str] = frozenset({"monotonic", "sleep"})


def _time_bindings(tree: ast.AST) -> tuple[set[str], dict[str, str]]:
    """The names a module binds to the ``time`` module, and to its functions (local name -> function name)."""
    modules: set[str] = set()
    functions: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update((alias.asname or alias.name) for alias in node.names if alias.name == "time")
        elif isinstance(node, ast.ImportFrom) and node.module == "time" and node.level == 0:
            functions.update({(alias.asname or alias.name): alias.name for alias in node.names})
    return modules, functions


def _time_call_lines(source: str, time_functions: frozenset[str]) -> list[int]:
    """Lines of calls to any of ``time_functions`` of the ``time`` module, however it was imported. Only calls: a
    bare attribute load (``timer = time.monotonic``) is a default a collaborator can still replace."""
    tree = ast.parse(source)
    modules, functions = _time_bindings(tree)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        through_module = (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id in modules
            and func.attr in time_functions
        )
        through_import = isinstance(func, ast.Name) and functions.get(func.id) in time_functions
        if through_module or through_import:
            lines.append(node.lineno)
    return lines


def _wall_clock_read_lines_in(source: str) -> list[int]:
    tree = ast.parse(source)
    lines = _time_call_lines(source, _WALL_CLOCK_TIME_FUNCTIONS)
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
    return sorted(lines)


def _wall_clock_read_lines(path: Path) -> list[int]:
    return _wall_clock_read_lines_in(path.read_text())


def _ambient_monotonic_call_lines(path: Path) -> list[int]:
    return _time_call_lines(path.read_text(), _AMBIENT_TIME_FUNCTIONS)


def _is_compatibility_tooling(path: Path) -> bool:
    return _COMPATIBILITY_PACKAGE in path.relative_to(_SRC_DIR).parts[:-1]


def test_no_direct_wall_clock_read_outside_the_clock_implementation() -> None:
    """bzh:injected-clock: every timestamp flows through an injected ``IClock`` — a direct
    ``datetime.now()``, ``datetime.utcnow()``, ``time.time()``, ``time.time_ns()``, or ``func.now()`` read is a
    violation everywhere under ``src/blizzard`` except the clock's own implementation."""
    violations: list[str] = []
    for path in sorted(_SRC_DIR.rglob("*.py")):
        if path == _CLOCK_IMPLEMENTATION_FILE:
            continue
        for lineno in _wall_clock_read_lines(path):
            violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}")
    assert not violations, f"K — every timestamp must flow through IClock: {violations}"


def test_no_ambient_monotonic_read_or_sleep_outside_the_clock_implementation() -> None:
    """bzh:injected-clock: every mechanical deadline flows through an injected ``IMonotonicClock`` — a direct
    ``time.monotonic()`` or ``time.sleep()`` call is a violation everywhere under ``src/blizzard`` except the clock's
    own implementation and the hand-run OpenCode compatibility tooling."""
    violations: list[str] = []
    for path in sorted(_SRC_DIR.rglob("*.py")):
        if path == _CLOCK_IMPLEMENTATION_FILE or _is_compatibility_tooling(path):
            continue
        for lineno in _ambient_monotonic_call_lines(path):
            violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}")
    assert not violations, f"M — every mechanical deadline must flow through IMonotonicClock: {violations}"


@pytest.mark.parametrize(
    "source",
    [
        "import time\ntime.monotonic()\n",
        "import time\ntime.sleep(1)\n",
        "import time as t\nt.monotonic()\n",
        "import time as t\nt.sleep(0.1)\n",
        "from time import monotonic\nmonotonic()\n",
        "from time import sleep as nap\nnap(1)\n",
        "import time\nx = time.monotonic() + 1\n",
    ],
)
def test_ambient_monotonic_matcher_flags_every_import_form(source: str) -> None:
    assert _time_call_lines(source, _AMBIENT_TIME_FUNCTIONS)


@pytest.mark.parametrize(
    "source",
    [
        "import time\ntimer = time.monotonic\n",
        "import time\ndef f(sleep=time.sleep): ...\n",
        "import time\ntime.time_ns()\n",
        "from time import time_ns\ntime_ns()\n",
        "class time:\n    def monotonic(self): ...\ntime().monotonic()\n",
        "clock.monotonic()\nclock.sleep(1)\n",
        "import time as t\ntime.monotonic()\n",
    ],
)
def test_ambient_monotonic_matcher_admits_attribute_loads_and_other_calls(source: str) -> None:
    assert not _time_call_lines(source, _AMBIENT_TIME_FUNCTIONS)


@pytest.mark.parametrize(
    "source",
    [
        "import time\ntime.time()\n",
        "import time\ntime.time_ns()\n",
        "import time as t\nt.time()\n",
        "from time import time_ns as now_ns\nnow_ns()\n",
        "from datetime import datetime\ndatetime.now()\n",
    ],
)
def test_wall_clock_matcher_flags_every_import_form(source: str) -> None:
    assert _wall_clock_read_lines_in(source)


def test_wall_clock_matcher_admits_the_monotonic_reads() -> None:
    assert not _wall_clock_read_lines_in("import time\ntime.monotonic()\ntime.sleep(1)\nt = time.time\n")


@pytest.mark.parametrize(
    ("relative", "exempt"),
    [
        ("runner/harness/opencode/compatibility/probe.py", True),
        ("runner/harness/opencode_v2/compatibility/probe.py", True),
        ("runner/harness/opencode/adapter.py", False),
        ("runner/harness/compatibility.py", False),
    ],
)
def test_compatibility_exemption_is_a_package_segment(relative: str, exempt: bool) -> None:
    assert _is_compatibility_tooling(_SRC_DIR / relative) is exempt


# The composition-root set `bzh:dependency-injection`'s Do. declares
# (blizzard-context architecture/clean-architecture.md) — the only sites concrete wiring,
# `SystemClock()` included, may happen. One entry per root, each carrying its own
# justification; a CLI command is a short-lived process with no server loop to hand a
# wired dataclass through, so wiring inline at the top of the command body is that
# process's own composition root.
_CLOCK_COMPOSITION_ROOTS: dict[Path, str] = {
    _HUB_DIR / "app.py": "build_hosted_app — the hosted hub's composition root",
    _HUB_DIR / "composition.py": "build_hub_core and build_services — the hub's process-graph composition root",
    _HUB_DIR / "runtime.py": "init_environment, migrate and migration_runner — offline store administration's own root",
    _RUNNER_DIR / "app.py": "build_hosted_app — the runner's served-app composition root",
    _RUNNER_DIR / "composition.py": "build_runner_process — the runner's hosted process graph",
    _HUB_DIR / "cli" / "traces.py": "a short-lived CLI process's own composition root",
    _HUB_DIR / "cli" / "egress.py": "a short-lived CLI process's own composition root",
    _SRC_DIR / "cli" / "collaborators.py": "CliCollaborators.production — every CLI process's collaborator root",
    _FOUNDATION_DIR / "clock.py": "resolve_clock — the clock fence's resolver, where a serving root's clock is chosen",
    _SRC_DIR
    / "tools"
    / "invariants.py": "hub_invariants and runner_invariants — the invariant checker's composition root",
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
    — it may be constructed only at a declared composition root, never inside
    a collaborator it is handed down to."""
    violations: list[str] = []
    for path in sorted(_SRC_DIR.rglob("*.py")):
        if path in _CLOCK_COMPOSITION_ROOTS:
            continue
        for lineno in _system_clock_construction_lines(path):
            violations.append(f"{path.relative_to(_REPO_ROOT)}:{lineno}")
    assert not violations, f"L — SystemClock() may be constructed only at a composition root: {violations}"
