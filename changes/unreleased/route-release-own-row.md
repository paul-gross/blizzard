---
kind: fixed
surfaces: [http-api, board]
---

A reloaded activity feed no longer shows a `detached` row for a stop, completion, requeue, migration, hub step, or
delivery that released a held chunk. Each reads as that fact's own row, as it did live. Releases recorded before this
change still read `detached`.
