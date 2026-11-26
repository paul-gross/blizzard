---
kind: fixed
---

A runner on a `winter` workspace that vendors `tools/winter-cli` runs it with `uv` alone and no longer calls `mise`, so
a host with a recent `mise` claims work again instead of failing every tick.

## Upgrade

Make `uv` reachable on the `PATH` the runner host gives its `winter` calls, through the service unit or `[tools]`
`path_prepend`. A host that reached `uv` only through `mise` fails to prepare environments until you do.
