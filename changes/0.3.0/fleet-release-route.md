---
kind: added
surfaces: [http-api, wire, board]
---

A runner can give back a chunk it holds mid-graph: `POST /api/fleet/chunks/{chunk_id}/release` frees the route, and the
chunk returns to the queue `ready` at its current node, as after a detach, with no retry spent. The request carries the
epoch the runner last saw, its route token, and a reason the hub records but never interprets. The hub refuses a stale
epoch or a chunk that is not running with `409`, a caller that does not hold the route with `403`, and an unknown chunk
with `404`. The board's activity feed reads the release as `released (<reason>)`, naming the runner that let go, and the
`chunk-changed` cause vocabulary gains `released`.
