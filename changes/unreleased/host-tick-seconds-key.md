---
kind: added
surfaces: [config]
---

A timed host's interval is now the host file's top-level `tick_seconds`, in seconds, default 30: each runner's tick and
the host's own pass wait that long from the end of one pass to the start of the next. The host reads it at start. The
configuration contract documents also give every key a `lifetime` (when a changed value takes hold) and, where a
variable supplies it, its `env`.
