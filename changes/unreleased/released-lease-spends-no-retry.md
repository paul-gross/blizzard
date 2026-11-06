---
kind: fixed
surfaces: [store]
---

A runner set to `on_usage_limit = "release"`, and a chunk detached back onto its runner, gave the chunk one fewer retry
at that step. A released lease now spends no retry.
