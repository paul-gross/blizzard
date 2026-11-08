---
kind: fixed
surfaces: [config]
---

Worker scratch paths are shorter by default, avoiding Unix socket path-length failures such as Postgres failing to start
inside a worker. The host can also set `[worker_scratch] root` to a shorter accessible path.

## Upgrade

Existing active leases keep their legacy scratch directories until they close. Before changing the effective scratch
root, drain the host's active leases; the host refuses to switch roots while any remain active and reconciles the old
root after they drain. If the change is refused, restore the previous setting and drain before trying again.
