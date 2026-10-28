---
kind: changed
surfaces: [cli]
---

`blizzard runner tick` now steps a running host instead of refusing it: when the host answers on its socket, the pass
runs on the host through the runner `--runner` names (or the host's only one), and the command prints `tick complete`
or the reported failure and exits 0 only when the pass completed. `blizzard runner tick --host` steps the host-wide
pass and is refused when no host is running; it cannot be combined with `--runner`. With no host serving, the pass
still runs in the command's own process.
