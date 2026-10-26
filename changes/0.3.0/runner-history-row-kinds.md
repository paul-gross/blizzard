---
kind: changed
surfaces: [http-api]
breaking: true
breaks:
  - HistoryRowView.kind
---

The runner's chunk-history rows (`GET /leases/{lease_id}/history`) gain the kinds `resolution`, `requeue` and `restart`.
A `0.2` worker CLI prints such rows as plain text; no `0.2` panel reads the route.

## Upgrade

Nothing to do: a `0.2` worker reading the history of a chunk that carries the new kinds sees them as plain text.
