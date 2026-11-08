---
kind: fixed
surfaces: [event-stream]
---

A diverged fast-forward landing on a forge that lacks the branch operations it needs escalated the chunk without the
`forge-capability-missing` warning. The refusal now records the warning, naming the repository, the forge and the
capability, like a capability missing before the pass.
