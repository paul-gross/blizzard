"""No test chooses the port a daemon will bind (``bzh:daemon-binds-port-zero``).

A port chosen test-side — bound to ``0``, read, released, then handed to a daemon — is free only
until someone else binds it, and under xdist someone else does. Every daemon binds ``--port 0`` and
publishes where it landed with ``--listening-file``; an in-process server binds a socket it holds.
This test scans the Python and TypeScript test trees for a reintroduced port chooser.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_TESTS_DIR = Path(__file__).resolve().parent

#: Resolved, because ``rglob`` yields paths under the already-resolved ``_TESTS_DIR``, or this file flags
#: its own docstring under a symlinked checkout.
_SELF = Path(__file__).resolve()

#: A port chooser by name, in either language: ``free_port``, ``freePort``, ``otelcolFreePort``.
_CHOOSER_NAME = re.compile(r"free_?port", re.IGNORECASE)

#: A TypeScript bind-probe-close: a server listening on ``0`` whose close resolves the port it read.
_TS_PROBE = re.compile(r"\.listen\(\s*0\b[\s\S]{0,300}?\.close\(\s*\(\)\s*=>\s*resolve\(\s*port\s*\)")

#: The two port choices the tiers keep — each the only way to start what it starts:
#: a released baseline daemon predating ``--listening-file`` (until every supported release ships it),
#: and otelcol, which takes its ports from config and publishes none.
_ALLOWED_PY = {("rehearsal/installation.py", "baseline_daemon_port")}
_ALLOWED_TS_FILES = {"harness/collector.ts"}


def _returns_a_released_port(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Whether ``function`` returns a ``getsockname()`` read from inside a ``with`` that closes the socket."""
    for node in ast.walk(function):
        if not isinstance(node, ast.With):
            continue
        if not any("socket" in ast.unparse(item.context_expr) for item in node.items):
            continue
        for inner in ast.walk(node):
            if isinstance(inner, ast.Return) and inner.value is not None and "getsockname" in ast.unparse(inner.value):
                return True
    return False


def _python_offenders(rel: str, text: str) -> list[str]:
    """``rel``'s port choosers: a function named for one, or one returning a port it released."""
    offenders = []
    for node in ast.walk(ast.parse(text, filename=rel)):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) or (rel, node.name) in _ALLOWED_PY:
            continue
        if _CHOOSER_NAME.search(node.name) or _returns_a_released_port(node):
            offenders.append(f"{rel}:{node.lineno} {node.name}")
    for lineno, line in enumerate(text.splitlines(), start=1):
        code = line.split("#", 1)[0]
        if re.search(r"\bfree_port\s*\(", code) and f"{rel}:{lineno} " not in " ".join(offenders):
            offenders.append(f"{rel}:{lineno} calls free_port")
    return offenders


def _typescript_offenders(rel: str, text: str) -> list[str]:
    """``rel``'s port choosers: a line naming one, or a listen-on-0 whose close resolves the port."""
    if rel in _ALLOWED_TS_FILES:
        return []
    offenders = [
        f"{rel}:{lineno}" for lineno, line in enumerate(text.splitlines(), start=1) if _CHOOSER_NAME.search(line)
    ]
    offenders += [f"{rel}:{text.count(chr(10), 0, match.start()) + 1} probe" for match in _TS_PROBE.finditer(text)]
    return offenders


def _scanned(root: Path, suffix: str) -> list[tuple[str, str]]:
    """Every ``suffix`` file under ``root``, as ``(path relative to the tests tree, text)``."""
    return [
        (path.relative_to(_TESTS_DIR).as_posix(), path.read_text())
        for path in sorted(root.rglob(f"*{suffix}"))
        if path != _SELF and "node_modules" not in path.parts
    ]


def test_no_test_chooses_a_port_for_a_daemon_to_bind() -> None:
    offenders = [hit for rel, text in _scanned(_TESTS_DIR, ".py") for hit in _python_offenders(rel, text)]
    offenders += [
        hit for rel, text in _scanned(_TESTS_DIR / "harness", ".ts") for hit in _typescript_offenders(rel, text)
    ]
    assert offenders == [], (
        "these choose a port test-side for a daemon to bind, which another worker can take first; start the "
        "daemon on --port 0 with --listening-file and read its URL, hold a bound socket for an in-process "
        f"server, or take the refused_url fixture for an address nothing listens on: {offenders}"
    )


_PLANTED_PY = """import socket


def pick() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
"""

_PLANTED_TS = """function pick(): Promise<number> {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address() as net.AddressInfo;
      server.close(() => resolve(port));
    });
  });
}
"""


def test_the_guard_flags_a_bind_probe_close_by_shape_in_either_language() -> None:
    assert _python_offenders("crash/planted.py", _PLANTED_PY) == ["crash/planted.py:4 pick"]
    assert _typescript_offenders("harness/planted.ts", _PLANTED_TS) == ["harness/planted.ts:4 probe"]


def test_the_guard_flags_a_port_chooser_by_name_and_spares_only_the_two_kept() -> None:
    assert _python_offenders("service/planted.py", "x = free_port()\n") == ["service/planted.py:1 calls free_port"]
    assert _typescript_offenders("harness/planted.ts", "const port = await freePort();\n") == ["harness/planted.ts:1"]
    assert _typescript_offenders("harness/collector.ts", _PLANTED_TS) == []
    kept = _PLANTED_PY.replace("def pick", "def baseline_daemon_port")
    assert _python_offenders("rehearsal/installation.py", kept) == []
    assert _python_offenders("rehearsal/other.py", kept) == ["rehearsal/other.py:4 baseline_daemon_port"]
