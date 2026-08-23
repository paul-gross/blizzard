# Migrating a chunk to another graph

`blizzard hub chunk migrate <chunk-id> --to-graph <graph> [--node <name>]` sets a standing intent to move the chunk onto
another graph; `blizzard hub chunk migrate <chunk-id> --cancel` clears one. The two forms are exclusive — `--cancel`
cannot be combined with `--to-graph`/`--node`, and `--to-graph` is required unless `--cancel`; a PATCH of
`intended_migration` is the API equivalent. The intent is consulted at the chunk's next transition and never applied
eagerly — the current attempt runs to its normal verdict. `intended_migration` is editable at any non-terminal status,
claimed or not, which makes it the only way to move a chunk that has already run — the complement of the never-moved
graph re-pin ([editing.md](./editing.md)).

## Setting, aiming, and clearing an intent

`--to-graph` takes a graph id or a name, a name resolving to the newest enabled graph of that name. A blank name is 422;
a retired target, or one equal to the current pin, is 409. Edits stay all-or-nothing: a PATCH naming
`intended_migration` alongside a claimed chunk's sealed `graph_id` is refused entirely, on `graph_id`. `--cancel`, or a
PATCH of `intended_migration: null`, clears the intent without firing it.

An intent aims at one resolved mint id, deliberately: a later mint under the same name never silently redirects a
hand-aimed chunk. The cost is that each workflow edit strands in-flight chunks on old mints until they are individually
migrated — the follow-latest policy below is the standing alternative.

## How an intent fires

Without `--node` the intent is auto: it fires only when the transition's own destination node name also exists on the
target, landing there; otherwise the transition applies unchanged and the intent stays set. `--node` makes it forced: it
fires unconditionally at the next transition, landing on the named node whatever the transition's own destination — with
a 409 up front if the node is absent from the target.

A fired intent is recorded as a migration, like an authored cross-graph judgement choice: it re-pins the graph, lands on
the resolved node, and clears the intent, atomically. The landed node's executor governs — landing on a hub-executed
node derives `delivering`, as any transition into one does.

## Following the latest mint

`follow_latest` replaces hand-aimed intents after workflow edits: a chunk pinned to a follow-latest graph re-pins to the
newest enabled mint of its own graph's name at its next transition. The policy has two levels, the graph winning where
it speaks: the `blizzard-hub.toml` key `follow_latest` (`true`/`false`, default `false`) is the fleet-wide default for
graphs that say nothing, and `blizzard hub graph follow-latest <graph-id> true|false|inherit` is the mint's override —
`inherit`, every mint's default, defers to the hub key. `GET /api/graphs/{id}` serves the stored tri-state as-is:
says-nothing is distinguishable from says-false.

The policy is per mint, not per name: a chunk consults its pinned graph's policy, so arming a lineage means arming the
mint its chunks sit on — or the hub default, which covers every name. It is a plain no-op — no error, no fact — when the
effective policy resolves false (the default configuration's ordinary case), when the chunk is already on the newest
mint, or when every newer mint is retired; and it never moves a chunk backwards: a chunk whose own mint was retired
stays put when name resolution answers an older one. An explicit `intended_migration` outranks the policy entirely —
even on a transition where an auto intent falls through for want of a name match.

A follow-latest move rides the same deferred path with the same guarantees — recorded as a migration fact, not disguised
as a transition, and firing only at a transition the chunk was making anyway. Its landing is name-match-else-entry on
the transition's own destination, falling back to the target's entry node when the newer definition lost the node.

## When there is no transition to wait for

`chunk restart --to-graph` ([control-verbs.md](../control-verbs.md)) is the eager counterpart, for when no next
transition is worth waiting for.
