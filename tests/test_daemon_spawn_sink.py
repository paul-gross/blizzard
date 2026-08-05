"""No test spawns a long-lived daemon onto a pipe nothing drains (``bzh:daemon-stdout-to-file``).

``stdout=subprocess.PIPE`` on a daemon no one reads from deadlocks once its output fills
the pipe buffer. This test greps for a new spawn site reintroducing the pipe.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_TESTS_DIR = Path(__file__).resolve().parent

# ``capture_output=True`` implies PIPE without naming it, so the literal below only ever
# matches an explicit hand-wired pipe.
_PIPE = re.compile(r"stdout\s*=\s*subprocess\.PIPE")

#: The escape hatch: a genuinely-read pipe annotates itself on the same line and is
#: skipped, rather than a blanket ban forcing it to weaken the guard.
_DRAINED = "# drained-pipe: this spawn reads its own stdout"

#: Resolved, because ``rglob`` yields paths under the already-resolved ``_TESTS_DIR``, or
#: this file flags its own docstring under a symlinked checkout.
_SELF = Path(__file__).resolve()


def test_no_test_spawns_a_daemon_onto_an_undrained_pipe() -> None:
    offenders = [
        f"{path.relative_to(_TESTS_DIR)}:{lineno}"
        for path in sorted(_TESTS_DIR.rglob("*.py"))
        if path != _SELF
        for lineno, line in enumerate(path.read_text().splitlines(), start=1)
        if _PIPE.search(line) and _DRAINED not in line
    ]
    assert offenders == [], (
        "these spawn sites pipe a daemon's stdout to a buffer nothing drains; "
        f"use tests.support.daemon_log_sink instead: {offenders}"
    )
