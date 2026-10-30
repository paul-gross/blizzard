---
kind: changed
surfaces: [cli]
---

Every `blizzard hub` verb that reads one thing is now named `get`: `hub chunk get`, `hub flow get`, `hub forge get`, and
the rest, and `blizzard runner prompt get`. Every group with a `get` also accepts `show` and `read` as hidden aliases
that behave identically and appear in neither `--help` nor completion, so a `show` typed today keeps working. The
recorded CLI surface in `contracts/cli/` lists each group's `aliases`.
