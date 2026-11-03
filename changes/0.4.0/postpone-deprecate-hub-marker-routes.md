---
kind: changed
surfaces: [http-api]
postpones: deprecate-hub-marker-routes
removes-in: "0.5"
---

The hub marker and delivery routes `POST /api/chunks/{chunk_id}/hub-markers`, `garden-delivery` and
`review-findings-delivery` stay deprecated through 0.4 and are now removed in 0.5, together with the other deprecations
0.3 and 0.4 carry, so the skew window's floor rises once.
