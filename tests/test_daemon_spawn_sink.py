"""No test spawns a long-lived daemon onto a pipe nothing drains (issue #145).

The static guard behind ``bzh:daemon-stdout-to-file``. ``stdout=subprocess.PIPE`` on a
daemon no one reads from is a deadlock on a timer: the process runs until its output
fills the ~64 KiB pipe buffer, then blocks in ``write`` forever — alive to ``poll()``,
still accepting connections on its port, and serving nothing. Because the fuse is output
*volume*, a suite arms it without changing: adding one log line in ``src/`` can wedge a
tier that has passed for months, and the failure surfaces as a timeout far from its
cause.

The four daemon-running tiers all spawn through ``tests.support.daemon_log_sink``
instead. This test is what keeps a new spawn site from quietly reintroducing the pipe —
the ACs' own ``grep``, run as a test so it fails in CI rather than in review. A
short-lived ``subprocess.run(..., capture_output=True)`` drains by construction and is
not matched.
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


def test_no_test_spawns_a_daemon_onto_an_undrained_pipe() -> None:
    offenders = [
        f"{path.relative_to(_TESTS_DIR)}:{lineno}"
        for path in sorted(_TESTS_DIR.rglob("*.py"))
        if path != Path(__file__)
        for lineno, line in enumerate(path.read_text().splitlines(), start=1)
        if _PIPE.search(line)
    ]
    assert offenders == [], (
        "these spawn sites pipe a daemon's stdout to a buffer nothing drains; "
        f"use tests.support.daemon_log_sink instead: {offenders}"
    )
