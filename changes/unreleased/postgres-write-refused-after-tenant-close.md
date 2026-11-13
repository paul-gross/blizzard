---
kind: fixed
surfaces: [http-api]
---

On a hub running Postgres, a write that was waiting on a tenant's deletion to begin could still land in the tenant once
the deletion started, and the tenant's removal then failed until a later pass cleared it. Such a write is now refused as
a write on a closed tenant, as it already was on SQLite.
