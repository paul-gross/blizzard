# Retiring and re-enabling graphs

Retiring brakes which graph a name resolves to; it stops no work. A graph carries no chunk, claim, or live worker, and a
chunk pinned to a retired graph runs it to completion. Only new resolution by name is blocked: the ingest default-graph
pin and a cross-graph migration's `graph:<name>` judgement target resolve to the newest non-retired graph of the name,
skipping retired graph_ids.

## The controls

`blizzard hub graph list`, `graph retire <graph_id>`, and `graph enable <graph_id>` — or the board graph explorer's
Retire/Re-enable buttons and lifecycle badge — are the retirement controls. Retire appends a `graph.retired` fact, and
`graph enable` reverses it with `graph.enabled`: the toggle is reversible, every flip is an append-only audit trail, and
the graph's immutable row is untouched either way. The other graph-level lifecycle control,
`blizzard hub graph
follow-latest`, is owned by [migration.md](./migration.md).

## A name with nothing left to resolve

Retiring every graph ever minted under a name — the packaged default-delivery included — leaves resolution nothing: an
ingest that would lazily mint a fresh packaged-default copy refuses 503, since a fresh mint would silently undo the
retire — including across a hub restart. A cross-graph migration choice naming an all-retired target hits the same
nothing-to-resolve refusal when a chunk takes it. Clear an all-retired name by re-enabling a retired version or minting
a new graph under it.
