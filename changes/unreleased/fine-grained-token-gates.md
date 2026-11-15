---
kind: changed
surfaces: [event-stream]
---

A check read GitHub refuses for a fine-grained token (`github_pat_…`), as it does on a private repository, is reported
as `token-unsupported`, naming the credential and the capability it needs. The `forge-capability-missing` event gains
`credential`, and the budget notices gain `authority`.
