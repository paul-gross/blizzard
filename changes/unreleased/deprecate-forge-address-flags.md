---
kind: deprecated
surfaces: [cli]
removes-in: "0.4"
---

`blizzard hub repo`'s `--forge-api-url`, and `blizzard hub source`'s `--api-base` and `--web-base` and
`--clear api-base` and `--clear web-base`, are deprecated; name the forge with `--forge`. Until they are removed, each
is resolved to the forge holding that origin and prints a warning.
