---
kind: changed
surfaces: [store]
---

The hub store gains a `hub_locks` table for locks on concepts no tenant owns. The identity lock moves onto it from the
store's revision row, and creating a tenant now also takes a tenant-lifecycle lock. Closing a tenant holds only that
tenant's row and the tenant-lifecycle lock, where before it locked every tenant's row. Tenant renames, closes, and the
last-open-tenant refusal answer as before.

## Upgrade

Run `blizzard hub migrate` before starting the new build. The revision creates and seeds the table; a hub started on an
unmigrated store refuses to write identities or tenants until it is.
