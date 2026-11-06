---
kind: added
surfaces: [cli]
---

A runner on OpenCode 2 offers it only when it can run a turn. Its health checks the operator's managed service in order
and names the first check that fails in the runner's log: a service the operator has disabled, a service that does not
answer once `opencode service start` has run, a binary and service of different major versions, a configured tier whose
model the service lists through no provider it is signed in to, or a configured effort variant that model does not
offer. Health starts a service that is not running, never one the operator has disabled, and is recalculated when a
launch is refused because the service's version changed. A runner on OpenCode 1 reports itself unavailable when OpenCode
2 has migrated its store, naming the store. `blizzard runner selftest opencode` runs against either major version.
