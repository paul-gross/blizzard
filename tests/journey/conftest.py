"""Gate for the capstone journey rehearsal (``blizzard:journey``).

Like the kill-9 sweep it drives the daemons as **real subprocesses** over the mock
fleet: skipped unless ``BLIZZARD_JOURNEY=1`` and the sibling worktree + local winter
source are discoverable.
"""

from __future__ import annotations

# The gate lives on the test module's ``pytestmark`` skipif, so it never touches the
# default suite's collection.
