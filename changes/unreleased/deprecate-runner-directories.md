---
kind: deprecated
surfaces: [config]
removes-in: "0.6"
---

A host directory's `runners/<name>/` is deprecated. `blizzard runner add` no longer creates it; only
`blizzard runner
ingest` still writes one, for the path files a moved runner's `[[runner]]` reads.
