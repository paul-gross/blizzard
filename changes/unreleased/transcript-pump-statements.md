---
kind: changed
surfaces: [store]
---

The runner ships transcripts with fewer store statements: each shipping window takes one sequence allocation and one
write however many records it holds, and each pass reads spawn settings once for all of its open transcripts. Sizing the
unshipped transcript backlog now reads a recorded size instead of each buffered record, and existing buffered records
have their size filled in when the store is migrated.

## Upgrade

Run `blizzard runner migrate` before starting the upgraded runner.
