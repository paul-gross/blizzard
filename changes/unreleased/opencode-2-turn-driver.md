---
kind: added
surfaces: [cli]
---

`blizzard runner opencode drive --request PATH` drives one OpenCode 2 turn on the operator's managed OpenCode service
and prints its result. It is the worker entrypoint the OpenCode 2 binding launches for each spawn, resume, judgement,
and nudge, and is not meant to be run by hand.
