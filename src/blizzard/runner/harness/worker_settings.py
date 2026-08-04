"""The runner-owned worker hook file.

:func:`worker_settings_document` is the single source of its content. The
``PostToolUse`` hook fires ``blizzard runner heartbeat`` on every tool call; the
``SessionEnd`` hook fires ``blizzard runner session-end`` when the session exits. Both
verbs take their identity from the spawn environment (``BLIZZARD_LEASE_ID`` /
``BLIZZARD_RUNNER_URL``), so the hook commands need no arguments.

The file ships with the runner and is versioned with it — nothing is materialized into
a project repo (repos know nothing about the fleet).
"""

from __future__ import annotations

import json
from typing import Any

#: The command a worker's PostToolUse hook runs — a pure client of the local API.
HEARTBEAT_HOOK_COMMAND = "blizzard runner heartbeat"
#: The command a worker's SessionEnd hook runs — the "declared done" signal.
SESSION_END_HOOK_COMMAND = "blizzard runner session-end"


def worker_settings_document() -> dict[str, Any]:
    """The worker hook set as a Claude Code settings document (the ``--settings`` file)."""
    return {
        "hooks": {
            "PostToolUse": [
                {"hooks": [{"type": "command", "command": HEARTBEAT_HOOK_COMMAND}]},
            ],
            "SessionEnd": [
                {"hooks": [{"type": "command", "command": SESSION_END_HOOK_COMMAND}]},
            ],
        },
    }


def worker_settings_json() -> str:
    """The worker settings document rendered as the JSON written to disk."""
    return json.dumps(worker_settings_document(), indent=2) + "\n"
