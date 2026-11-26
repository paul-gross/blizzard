---
kind: changed
surfaces: [http-api, board]
---

The hub records the signed-in person as who paused, resumed, stopped, completed, restarted or deleted a chunk, declared
or released a dependency, acted on a runner or host, or retired or enabled a cadence, routine or scope — `operator` when
auth is off — whatever name the request supplies. A runner a host retires under itself is recorded as retired by that
host.
