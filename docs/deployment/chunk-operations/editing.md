# Editing an unclaimed chunk

While a chunk is unclaimed — resting `not_ready`, or promoted `ready` with no runner holding it — its pinned graph and
its default model/effort are editable via `PATCH /api/chunks/{id}`; once claimed or later (`running`, `delivering`,
`waiting_on_human`, `needs_human`, post-claim `paused`, `done`, `stopped`) those edits are refused 409. The PATCH
applies any of `graph_id`, `default_model`, `default_effort`, and `intended_migration` in one all-or-nothing request: a
supplied field outside its own editable window refuses the whole request — a 409 naming the field, except the
already-moved refusal, which names the chunk and points at migration — and nothing in the body is applied.

`blizzard hub chunk set` takes `--default-model` repeatably and ordered, plus `--default-effort`; there is deliberately
no web editing surface for the defaults, so `chunk show` — or the detail payload's `default_model`/`default_effort`
fields — is the read-back. The two chunk defaults are what a surface declaring neither inherits, at precedence graph
`sessions:` declaration, then chunk default, then the runner's own default; `default_model` is a prioritized preference
list in the same vocabulary as a session declaration — `blizzard:` tier aliases or harness-native names, resolved left
to right at session mint — and `default_effort` is a single value. A blank model entry is 422; an empty list and an
explicit null effort are real values meaning express-no-preference — what the state ingest mints — not leave-unchanged.
Neither default vocabulary is validated hub-side, since the alias tables live in each runner's own config — both are
opaque preference strings to the hub.

The graph edit carries one further condition the defaults do not: the chunk must also never have moved, and unclaimed
and never-moved are different tests — a chunk that was claimed, ran a node, and was detached derives `ready` again while
still standing on a node of its pinned graph, and re-pinning it would strand that node absent from the new graph, so the
edit is refused 409 even though the chunk is unclaimed. The graph edit has two further 409s beyond the status window: a
retired target graph (named by its retired graph id) and the already-moved refusal; the already-moved check runs first,
so a moved-but-unclaimed chunk aimed at a retired graph reports the move, not the retirement.

Moving a chunk that has run is not an edit's job: which graph it is on at its next transition is `chunk migrate`
(below); where it stands right now, or which graph it is on right now, is `chunk restart`
([control-verbs.md](../control-verbs.md)); the defaults name no node to strand, so they stay editable for as long as the
chunk is unclaimed.
