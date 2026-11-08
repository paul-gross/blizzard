---
kind: fixed
surfaces: [http-api]
---

A direct claim on a chunk is now refused with `409` and `reason: binding` when the runner's binding for the chunk's
project is flagged against its host's latest report, its subscription is outside the host's roster, or the claim's
workspace is not the one the runner binds that project to, as the matched peek would not have offered it.
