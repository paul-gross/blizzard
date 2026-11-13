---
kind: added
---

When GitHub refuses a repository's or work source's token, blizzard now says why. The `forge-auth` escalation's detail
names the failed operation, the HTTP status, GitHub's message, its request id, and the remedy. A new
`forge-auth-refused` event in the event log records each refusal, and a work item whose source is refused shows the
refusal and its remedy in its `error`. The token never appears in any of them.

The forge token docs no longer promise that a fine-grained token reads checks; use a classic token for delivery.
