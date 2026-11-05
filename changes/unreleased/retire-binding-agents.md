---
kind: removed
surfaces: [config, wire]
breaking: true
retires: deprecate-binding-agents
---

`runner[].projects[].agents` is removed: a host file that sets `agents` on a `[[runner.projects]]` binding is refused at
load, naming `[[runner]] agents`. The runner no longer sends a binding's `agents` or the matched peek's `project`, and
the hub no longer reads, stores or derives anything from either; the peek narrows by `projects` alone.

## Upgrade

Delete `agents` from each `[[runner.projects]]` binding and set `[[runner]] agents` to the runner's total.
