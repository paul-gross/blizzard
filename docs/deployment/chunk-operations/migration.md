# Migrating a chunk between graphs

Aiming a chunk at another graph without interrupting what it is doing — the standing intent an operator sets by hand,
and the policy that keeps a lineage on its newest mint.

## Migration intents

`blizzard hub chunk migrate <chunk-id> --to-graph <graph> [--node <name>] [--cancel]` — or `PATCH` `intended_migration`
— sets a standing intent to move the chunk onto another graph, consulted, never applied eagerly, at the chunk's next
transition: the current attempt runs to its normal verdict, and `chunk restart --to-graph` is the eager counterpart for
when no next transition is worth waiting for.

`--to-graph` names a graph id or a name resolved to the newest enabled graph of that name; a blank name is 422, and a
retired target or one equal to the chunk's current pin is 409. An intent aims at one resolved mint id deliberately, so a
later mint under the same name never silently redirects a chunk an operator aimed by hand; the cost is that every
workflow edit strands the fleet on old mints until each in-flight chunk is migrated individually.

With no `--node` the intent is auto: it fires only when the transition's own destination node name also exists on the
target graph, landing there; with no name match the transition applies unchanged and the intent stays set for next time.
`--node` makes the intent forced: it fires unconditionally at the next transition, landing on the named node regardless
of the transition's own destination — refused 409 up front if that node does not exist on the target. `--cancel`, or
`PATCH intended_migration: null`, clears a standing intent without firing it.

When the intent fires, the movement is recorded as a migration exactly like an authored cross-graph judgement choice: it
re-pins the chunk's graph, lands it on the resolved node, and clears the intent in the same write; the landed node's own
executor governs — landing on a hub-executed node derives `delivering`, as a transition into one does.

The intent is a plain mutable chunk property consulted only at transitions, which is why it is editable at any
non-terminal status and why it is the only way to move a chunk that has already run — complementing the never-moved
graph repin. Because `intended_migration` is editable at any non-terminal status, claimed or not, a PATCH naming it
alongside a claimed chunk's now-sealed `graph_id` still refuses everything on `graph_id`.

## The `follow_latest` policy

Aiming intents by hand after every workflow edit is a chore; `follow_latest` is the standing policy that removes it: a
chunk pinned to a follow-latest graph re-pins to the newest enabled mint of its own graph's name at its next transition.

The policy has two levels, the graph winning where it speaks: the `blizzard-hub.toml` key (true or false, default false)
is the fleet-wide default for every graph that says nothing, and
`blizzard hub graph follow-latest <graph-id> true|false|inherit` is the mint's own override, `inherit` — every mint's
default — deferring to the hub. The policy is per mint, not per name: a chunk consults the policy of the graph it is
pinned to, so arming a lineage means arming the mint its chunks sit on — or setting the hub default, which covers every
name at once; `GET /api/graphs/{id}` serves the stored tri-state as-is, so a reader can tell says-nothing from
says-false.

`follow_latest` rides the same deferred path as an explicit intent, with the same guarantees — nothing in flight is
interrupted, the move is recorded as a migration fact rather than disguised as a transition, and it fires only at a
transition the chunk was making anyway; landing is name-match-else-entry on the transition's own destination, falling
back to the target's entry node when the newer definition no longer carries the node. An explicit `intended_migration`
outranks the policy, which is then not consulted at all — including on a transition where an auto intent falls through
for want of a name match.

The policy is a plain no-op — no error, no fact — when the chunk is already on the newest mint, when every newer mint is
retired, or when the effective policy resolves false; it never moves a chunk backwards, so a chunk whose own mint was
retired stays put when name resolution answers with an older one.
