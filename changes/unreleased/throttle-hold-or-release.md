---
kind: added
surfaces: [config]
---

A runner can give a chunk its throttles park back to the queue instead of waiting with it. Two keys in
`[runner.throttle]`, both defaulting to `"hold"`, choose: `on_decline = "release"` returns a chunk whose next step the
gate declines, `ready` at that step for whichever runner's throttles admit it, and `on_usage_limit = "release"` lets go
of a lease a usage limit parks mid-step while its floor still holds, keeping the half-done step's spend. Neither spends
a retry. A runner whose hub refuses the release, or predates it, keeps the chunk as with `"hold"`. Any other value halts
the runner and names the key. `blizzard runner status` and the runner panel show both choices.
