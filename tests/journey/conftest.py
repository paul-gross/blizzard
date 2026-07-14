"""Gate for the capstone journey rehearsal (``blizzard:journey``).

Like the kill-9 sweep it drives the daemons as **real subprocesses** over the mock
fleet, so it is a local, opt-in tier: skipped unless ``BLIZZARD_JOURNEY=1`` and the
sibling ``blizzard-mock`` worktree + a local winter source are discoverable. Run it::

    BLIZZARD_JOURNEY=1 uv run pytest -m journey
"""

from __future__ import annotations

# The gate lives on the test module's ``pytestmark`` (a ``skipif`` on ``BLIZZARD_JOURNEY``,
# exactly like the e2e tier), so it never touches the default suite's collection. This
# file exists only to mark the directory as a test package with its own scope.
