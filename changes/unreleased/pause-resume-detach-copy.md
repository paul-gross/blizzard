---
kind: changed
surfaces: [cli, board]
---

`chunk pause`, `chunk resume` and `chunk detach` now describe what happens to a chunk with and without a claim, and
detach names the open escalation or question that can still hold it. The fleet's pause control for an unclaimed chunk no
longer promises a parked session. `chunk show` omits the runner line for a `done` or `stopped` chunk.
