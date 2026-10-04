"""The commands a worker's lifecycle hooks run, whichever harness spawned it. Both verbs take
their identity from the spawn environment, so the commands need no arguments."""

from __future__ import annotations

#: The command a worker's PostToolUse hook runs — a pure client of the local API.
HEARTBEAT_HOOK_COMMAND = "blizzard runner heartbeat"
#: The command a worker's SessionEnd hook runs — the "declared done" signal.
SESSION_END_HOOK_COMMAND = "blizzard runner session-end"

__all__ = ["HEARTBEAT_HOOK_COMMAND", "SESSION_END_HOOK_COMMAND"]
