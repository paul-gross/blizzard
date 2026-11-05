---
kind: removed
surfaces: [http-api]
retires: deprecate-hub-marker-routes
breaking: true
breaks:
  - /api/chunks/{chunk_id}/hub-markers
  - /api/chunks/{chunk_id}/garden-delivery
  - /api/chunks/{chunk_id}/review-findings-delivery
  - HubMarkerRequest
  - HubMarkerResponse
  - GardenDeliveryRequest
  - GardenDeliveryResponse
  - ReviewFindingsDeliveryResponse
upgrade-guide: runners-below-04-are-refused
---

Removes `POST /api/chunks/{chunk_id}/hub-markers`, `POST /api/chunks/{chunk_id}/garden-delivery` and
`POST /api/chunks/{chunk_id}/review-findings-delivery`, with their request and response schemas. Only a hub node's
`run:` script called them; a hub node's action records its markers, its garden delivery and its review's findings. The
hub↔runner floor rises to 0.4, so the hub refuses a runner below it.

## Upgrade

Move every runner host onto 0.4 or later before the hub takes this release. Rewrite any client of your own that posts to
one of these routes onto the hub node's action that replaced it.
