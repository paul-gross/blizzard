# Retiring and re-enabling graphs

`blizzard hub graph list`, `graph retire <graph_id>`, and `graph enable <graph_id>` — or the graph explorer's Retire and
Re-enable buttons and lifecycle badge on the board — are an operator's brake over which graph a name resolves to, not a
work-stopping lever: a graph carries no chunk, claim, or live worker. A chunk already pinned to a retired graph keeps
running it to completion — retiring blocks only new resolution by name: the default-graph pin at ingest and a
cross-graph migration's `graph:<name>` judgement target both resolve through the newest non-retired graph of the name,
skipping every retired graph_id.

Retiring appends a `graph.retired` fact, reversed by `graph enable`'s `graph.enabled` fact — the graph's immutable row
is untouched, the brake is reversible, and every toggle is an append-only audit trail.

Retiring every graph ever minted under one name — including the packaged default-delivery the hub ingests against out of
the box — leaves name resolution nothing to hand back: the next ingest that would otherwise lazily mint a fresh copy of
the packaged default refuses with 503 instead, because a fresh mint would be immediately effective and silently undo the
retire, including across a hub restart. Clear the all-retired state by re-enabling one of the retired versions or
minting a new graph under the name; a cross-graph migration choice naming an all-retired target hits the same
nothing-to-resolve shape the moment a chunk takes it.
