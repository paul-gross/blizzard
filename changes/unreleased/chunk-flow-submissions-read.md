---
kind: added
surfaces: [http-api]
---

The hub serves `GET /api/chunks/{chunk_id}/flow-submissions`: every submission of a chunk's hub-node visits to the flows
they land on, in arrival order, each with its closure's outcome and time, or `null` while it is open. It is gated like
the other chunk reads, and another tenant's chunk answers 404.
