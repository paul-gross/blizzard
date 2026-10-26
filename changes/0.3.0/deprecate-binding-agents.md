---
kind: deprecated
surfaces: [config]
removes-in: "0.4"
---

`runner[].projects[].agents` is deprecated; set the runner's count with `runner[].agents`. The host logs the deprecation
while it sums per-binding values, and ignores them when `[[runner]] agents` is set.
