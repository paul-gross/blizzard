---
kind: added
surfaces: [http-api, wire, store, cli, board]
---

A person resolving a gate, requeueing a chunk at the hub, or restarting a chunk can leave a note for every later session
on it: `--note` on `blizzard hub decision resolve`, `blizzard hub chunk requeue` and `blizzard hub chunk restart`, and
an optional `note` on their endpoints. The hub requeue now takes an optional body and records who requeued. The chunk
detail lists the chunk's gate resolutions and hub requeues beside its restarts, each with its note. A blank note is no
note.

A worker reads every such note with `blizzard runner chunk notes`, and `blizzard runner chunk history` gains a row per
gate resolution, hub requeue and restart, each naming who made it and their note.
