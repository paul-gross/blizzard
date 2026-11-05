---
kind: added
surfaces: [http-api, wire, cli]
---

`blizzard runner artifact create` now refuses garden artifact content that fails the format its graph's delivery step
applies to that name, such as a garden routine's `delta` or `docket`. The refusal carries the delivery-time message, the
command exits non-zero, and any content staged earlier under the name stands. A new hub route,
`POST /api/fleet/chunks/{chunk_id}/artifact-check`, answers the check. If the hub cannot be reached, the content is
staged unchecked and delivery still validates it.
