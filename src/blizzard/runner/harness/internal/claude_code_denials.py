"""Claude Code's required headless denials.

Each harness denies tools for reasons specific to it; this list is Claude Code's alone and no
other binding inherits it."""

from __future__ import annotations

#: Tools that defer work to a future turn a headless worker never gets;
#: ``TaskOutput``/``TaskStop``/backgrounded ``Bash`` stay reachable on purpose.
CLAUDE_CODE_DENIED_TOOLS = (
    "ScheduleWakeup",
    "Monitor",
    "CronCreate",
    "CronDelete",
    "CronList",
    "RemoteTrigger",
    "EndConversation",
)
