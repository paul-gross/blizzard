---
kind: changed
surfaces: [store]
---

A runner shipping worker transcripts puts less load on its store each tick, and the load no longer grows with how many
transcripts are live or how much each one holds. The runner store now records each buffered transcript record's size;
the migration fills it in for records already buffered.

## Upgrade

Run `blizzard runner migrate --dir <host-directory>` before starting the upgraded host. If it is skipped, the host
refuses to start on the store revision mismatch and names that command.
