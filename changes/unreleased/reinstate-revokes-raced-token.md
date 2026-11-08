---
kind: fixed
surfaces: [http-api, cli]
---

Reinstating a runner now revokes any token it still carries, in the same step as the reversal, so a reinstated runner is
always unenrolled; before, a token minted by an enroll that raced the retire kept working after the reinstate. An enroll
that began before a retire or reinstate and finishes after it is now refused with `409` `runner-lifecycle-conflict` and
mints nothing, instead of handing out a working token.
