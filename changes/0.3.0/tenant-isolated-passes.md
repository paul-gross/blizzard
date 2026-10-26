---
kind: fixed
---

One tenant's failure or volume no longer changes what the hub's periodic passes do for another tenant. The forge-status
and event-derivation passes keep their change probe and last full pass per tenant, so a tenant whose source cannot be
read retries alone while every other tenant keeps skipping unchanged passes. A fault raised inside one tenant's share of
the forge-status, event-derivation or close-drain pass is logged against that tenant and the pass goes on to the next.
The close drain shares its per-pass limit fairly across the tenants holding due intents, so one tenant's backlog no
longer starves the others' closes.
