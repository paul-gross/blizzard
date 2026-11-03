---
kind: fixed
surfaces: [http-api, config, cli]
---

Two landing-order writes to one project at the same time could merge into an order neither asked for, two repositories
sharing a position among them. `PUT /api/projects/{project}/landing-order` and
`POST /api/projects/{project}/landing-order/position` now re-read and re-plan when another landing-order write lands
between their read and their write, and answer `409` with code `landing_order_conflict` only when that keeps happening.
A `POST /api/config/apply` whose document sets a project's `landing_order` is refused with `409` at that project's
entry, writing nothing, when another landing-order write for the project lands while the apply is planned; a dry run
reports the same refusal, and running the apply again plans it on the order that write left.
`blizzard hub project order` and `position` print the hub's refusal for that `409`.
