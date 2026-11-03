---
kind: fixed
surfaces: [config]
---

A runner restart on a multi-runner host no longer empties another runner's live worker scratch. Each runner's per-lease
scratch directories now sit under `worker-tmp/<runner_id>/`, and the start sweep covers only that subtree. A directory
left directly under `worker-tmp/` by an earlier version is removed only by the runner whose own store holds its lease,
once that lease has closed. `worker-stdout/`, `elicitation-output/` and `worker-tmp/<runner-id>` are now declared among
the host contract's durable paths.
