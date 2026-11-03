---
kind: added
surfaces: [http-api, wire]
---

The runner's local API serves `GET /api/leases/{lease_id}/usage`: the usage facts one lease recorded, oldest first, each
with its kind, spawn generation, node, epoch, model, harness, the four token counts, and its billed and estimated cost.
A lease the runner never minted returns 404.
