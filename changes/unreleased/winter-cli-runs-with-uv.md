---
kind: fixed
---

A runner on a `winter` workspace that vendors `tools/winter-cli` runs it with `uv` alone and no longer calls `mise`, so
a host with a recent `mise` claims work again instead of failing every tick.

## Upgrade

Make `uv`, and every other tool the workspace's `winter` hooks run, reachable on the `PATH` the runner host gives its
`winter` calls, through the service unit or `[tools]` `path_prepend`. Tools that `mise` supplied to those calls are no
longer added for you; one left off that `PATH` fails environment preparation.
