---
kind: fixed
surfaces: [cli]
---

The OpenCode 2 driver no longer fails a turn, interrupting every running tool call and subagent, when a tool call's
arguments take longer to generate than the driver allows its polls of the session to keep failing (a large patch or a
long subagent prompt). The transcript records such a call with its complete arguments once they finish.
