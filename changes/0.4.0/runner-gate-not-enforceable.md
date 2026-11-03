---
kind: added
surfaces: [http-api, wire, board, cli]
---

The hub now reports a runner gate that can never park a chunk. A gate naming a node that every active graph runs on the
hub, or that no active graph has, is flagged `unenforceable_gates` with the reason `hub-executed` or `unknown-node` in
`GET /api/runners` and `GET /api/runners/{id}`, on the Fleet tab's Gates badge, and in `blizzard runner status`. The
flag clears once a graph runs the node on a runner, and no gate is refused. `blizzard runner status` also lists the
runner's own open gate decisions under `gate decisions`, which needs the hub.
