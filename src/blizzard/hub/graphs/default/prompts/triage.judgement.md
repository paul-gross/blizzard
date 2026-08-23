If you have not yet submitted your triage rationale as the `triage-findings` asset, submit it now — it must be in place
before the verdict is recorded. The verdict then records the route the rationale justifies.

| Route          | Where it sends the chunk                                         |
| -------------- | ---------------------------------------------------------------- |
| `already-done` | Closes the chunk at the `done` terminal, without entering a lane |
| `basic`        | Migrates it to the `bas-dwf` lane                                |
| `advanced`     | Migrates it to the `adv-dwf` lane                                |
| `harness`      | Migrates it to the `bas-hwf` lane                                |

A migrating chunk lands at the target lane's entry node — `build` for `bas-dwf` and `bas-hwf`, `plan` for `adv-dwf`. A
migration is one-way: the chunk leaves this graph and does not come back.
