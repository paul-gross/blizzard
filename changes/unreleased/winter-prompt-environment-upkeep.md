---
kind: changed
---

The packaged `winter` workspace prompt now lets a worker maintain its own environment without asking: fast-forwarding a
worktree to its base, `winter provision`, and restarting the environment's services. When it rebases, it fast-forwards
the environment's other worktrees to their base too. It still keeps away from other environments, the source checkouts,
and the host's own runtime.

## Upgrade

A runner with `prompt_package = "winter"` picks up the new text after upgrading and restarting. An existing chunk sees
it at its next resumed node-step, announced as updated standing instructions.
