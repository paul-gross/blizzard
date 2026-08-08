"""The runner-owned worker hook file.

:class:`WorkerSettings` is the single source of its content. Both hook verbs take their
identity from the spawn environment, so the hook commands need no arguments. The file
ships with the runner — nothing is materialized into a project repo."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

#: The command a worker's PostToolUse hook runs — a pure client of the local API.
HEARTBEAT_HOOK_COMMAND = "blizzard runner heartbeat"
#: The command a worker's SessionEnd hook runs — the "declared done" signal.
SESSION_END_HOOK_COMMAND = "blizzard runner session-end"


@dataclass(frozen=True)
class WorkerSettings:
    """The worker hook set as a Claude Code settings document (the ``--settings`` file)."""

    heartbeat: str
    session_end: str

    @classmethod
    def of(cls) -> WorkerSettings:
        return cls(HEARTBEAT_HOOK_COMMAND, SESSION_END_HOOK_COMMAND)

    @property
    def document(self) -> dict[str, Any]:
        return {
            "hooks": {
                "PostToolUse": [
                    {"hooks": [{"type": "command", "command": self.heartbeat}]},
                ],
                "SessionEnd": [
                    {"hooks": [{"type": "command", "command": self.session_end}]},
                ],
            },
        }

    @property
    def json(self) -> str:
        """The document rendered as the JSON written to disk."""
        return json.dumps(self.document, indent=2) + "\n"
