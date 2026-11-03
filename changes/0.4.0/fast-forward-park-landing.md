---
kind: added
surfaces: [http-api, config, store, cli, board]
---

Repositories can land by fast-forwarding a checked revision without a pull request or by parking a change for a person
to merge. A parked visit reports its own waiting-since time, polls without spending the node's pending timeout, and
escalates if its change closes unmerged or exceeds the repository's park timeout. A schema-neutral store revision guards
rollback until non-default policies and open submissions using them are settled.
