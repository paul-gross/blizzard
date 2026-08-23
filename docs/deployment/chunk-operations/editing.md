# Editing an unclaimed chunk

A chunk's pinned graph and its default model and effort are editable through `PATCH /api/chunks/{id}` only while the
chunk is unclaimed — `not_ready`, or `ready` with no runner holding it; any other status refuses 409. The PATCH can
carry any of `graph_id`, `default_model`, `default_effort`, and `intended_migration`, all-or-nothing: a supplied field
outside its own editable window refuses the whole request — a 409 naming the field, except the already-moved refusal,
which names the chunk and points at migration — and nothing is applied. The CLI client is `blizzard hub chunk set`,
which requires at least one option.

## Re-pinning the graph

`blizzard hub chunk set --graph <graph-id>` re-pins the graph. `--graph`, like `PATCH graph_id`, resolves a graph id
only — never a name, unlike migration's `--to-graph`. The edit also requires that the chunk has never moved, and
unclaimed and never-moved are not the same thing: a chunk that was claimed, ran a node, and was detached derives `ready`
again while standing on a node of its pinned graph, and re-pinning it would strand that node, absent from the new graph
— so it is refused 409. A retired target graph is the graph edit's other extra 409, named by the retired graph id; the
already-moved check runs first, so a moved-but-unclaimed chunk aimed at a retired graph reports the move, not the
retirement.

When the window has closed, reach for the operations built for it: moving a chunk that has run is `chunk migrate`
([migration.md](./migration.md)), and moving one eagerly right now is `chunk restart`
([control-verbs.md](../control-verbs.md)).

## Default model and effort

`blizzard hub chunk set` takes `--default-model` repeatably — order-significant — plus `--default-effort`.
`default_model` is a prioritized preference list resolved left to right at session mint; `default_effort` is a single
value. The entry vocabulary — `blizzard:` tier aliases or harness-native names — the runner-side alias tables, and the
rule that the hub interprets neither default are owned by [worker-spawn.md](../worker-spawn.md); hub-side, both defaults
are opaque strings.

A blank `default_model` entry is 422; an empty list and an explicit null effort are real express-no-preference values,
not leave-unchanged. Neither is reachable from the CLI — `chunk set` omits an empty `--default-model` and an unset
`--default-effort` from the request body entirely — so expressing no preference takes the raw PATCH.

The defaults name no node to strand, so only the unclaimed window binds them. A session surface inherits them field by
field: independently for model and for effort, the precedence is the graph's `sessions:` declaration, then the chunk
default, then the runner default — so a `sessions:` entry declaring only `model` still inherits the chunk's
`default_effort`. Deliberately, no web surface edits the defaults; read-back is `chunk show` or the detail payload's
`default_model`/`default_effort` fields.
