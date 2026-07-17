"""The runner-owned worker hook file.

Hook delivery is spawn-scoped and adapter-owned: the Claude Code adapter passes this
settings file on the command line (``claude -p --settings <file>``), carrying the
worker hook set. The ``PostToolUse`` hook fires ``blizzard runner heartbeat`` on every
tool call — progress detection with no agent cooperation. The ``SessionEnd`` hook
fires ``blizzard runner session-end`` when the session exits, recording the "declared done"
signal startup crash-recovery reads after an involuntary restart. Both verbs
take their identity from the spawn environment (``BLIZZARD_LEASE_ID`` /
``BLIZZARD_RUNNER_URL``), so the hook commands need no arguments.

The file ships with the runner and is versioned with it — nothing is materialized into
a project repo (repos know nothing about the fleet), and a human's own ``claude``
session in the same worktree carries no fleet hooks. ``blizzard runner init`` writes it
into the runtime directory; :func:`worker_settings_document` is the single source of
its content. Future worker hooks (the ``AskUserQuestion`` deny) join the same
document without touching the adapter.
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
