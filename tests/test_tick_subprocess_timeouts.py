"""No ``subprocess.run`` a tick reaches may lack a ``timeout=`` (blizzard#443) — the
`test_daemon_spawn_sink.py` precedent, an AST fence rather than a comment's claim.

``PeriodicDriver.stop``'s unbounded join rests on every seam a tick touches being bounded;
this pins the four seams blizzard#443 audited (``judge`` no longer calls ``subprocess.run``
at all — it launches detached, so it is out of this fence's scope by construction)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parent.parent

#: Every tick-reached module whose `subprocess.run` calls must each carry `timeout=`.
_GUARDED_MODULES = (
    "src/blizzard/runner/loop/internal/subprocess_worktree_git.py",
    "src/blizzard/runner/loop/internal/subprocess_check_runner.py",
    "src/blizzard/runner/environments/internal/git.py",
    "src/blizzard/runner/environments/internal/winter_cli.py",
)


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
