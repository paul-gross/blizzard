"""No ``subprocess.run`` a tick reaches may lack a ``timeout=`` — the
`tests/test_daemon_spawn_sink.py` precedent, an AST fence rather than a comment's claim.

``PeriodicDriver.stop``'s unbounded join rests on every seam a tick touches being bounded;
this pins the launch seam's driver and the seam's own required timeout."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from blizzard.runner.process.child_processes import IChildProcesses
from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_REPO_ROOT = repo_root()

#: Every tick-reached module whose `subprocess.run` calls must each carry `timeout=` — the launch seam's driver,
#: the one runner module that imports `subprocess`.
_GUARDED_MODULES = ("src/blizzard/runner/process/internal/subprocess_child_processes.py",)


def _is_subprocess_run(node: ast.Call) -> bool:
    func = node.func
    if not isinstance(func, ast.Attribute) or func.attr != "run":
        return False
    return isinstance(func.value, ast.Name) and func.value.id == "subprocess"


def _unbounded_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    offenders = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and _is_subprocess_run(node)
            and not any(kw.arg == "timeout" for kw in node.keywords)
        ):
            offenders.append(node.lineno)
    return offenders


def test_every_tick_reached_subprocess_run_call_carries_a_timeout() -> None:
    offenders = {module: lines for module in _GUARDED_MODULES if (lines := _unbounded_calls(_REPO_ROOT / module))}
    assert offenders == {}, f"subprocess.run call(s) with no timeout=: {offenders}"


def test_the_launch_seams_run_to_completion_entry_requires_a_timeout() -> None:
    timeout = inspect.signature(IChildProcesses.run).parameters["timeout"]
    assert timeout.kind is inspect.Parameter.KEYWORD_ONLY
    assert timeout.default is inspect.Parameter.empty
    assert timeout.annotation == "float"
