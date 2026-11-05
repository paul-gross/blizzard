---
kind: added
surfaces: [trace]
---

OpenCode 2 sessions ship their transcripts: the runner reads each session it minted, and its children, through the
managed service, with a child's turns shown under the call that spawned it. A worker killed before reporting usage has
its tokens and cost summed from its transcript, and analytics counts OpenCode 2's file reads, skill invocations and
agent spawns.
