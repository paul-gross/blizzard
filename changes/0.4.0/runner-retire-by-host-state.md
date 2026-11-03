---
kind: changed
surfaces: [cli]
---

`blizzard runner retire` now forgets the retired runner's identity in the host's store only when the host is stopped.
With the host running, `retire` writes nothing to the store; the host forgets the identity itself when it halts the
runner at its next tick, once the hub refuses the runner's token as retired. `--runner` also takes the id of a runner
the host file no longer configures but the store still names, such as one left behind by a host crash: with the host
stopped it retires that runner at the hub and forgets its identity, and a running host refuses it, naming that it must
be stopped first.
