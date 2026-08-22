# Chunk operations

Moving a chunk between graphs, editing what an unclaimed chunk will run with, steering which graph a name resolves to,
and entering a parked chunk's session by hand.

## Editing an unclaimed chunk

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
([control-verbs.md](./control-verbs.md)); the defaults name no node to strand, so they stay editable for as long as the
chunk is unclaimed.

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

## Retiring and re-enabling graphs

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

## Taking over a parked session

`blizzard runner takeover <chunk_id>` continues a parked chunk's worker session interactively in your own terminal: it
records a takeover fact with the daemon first, so no loop step can respawn or judge the session while you hold it, then
execs the harness's resume command as your terminal's child, and marks the takeover ended when you exit, even on Ctrl-C.
Opening a takeover mints a fresh lease capability token, invalidating the previous one.

Run takeover as the service account, like every socket verb; [runner-doors.md](./runner-doors.md) owns that and the
`--dir`/`--runner-url` transports. On a split deployment run takeover on the runner's own host first: the wrong host
refuses with the not-held message even while the session is alive elsewhere.

The daemon hands the takeover a bounded environment — the lease's `BLIZZARD_*` identity vars plus its own `PATH` and
`HOME` — layered over your terminal's, so the session's `blizzard runner` verbs (`attach`, `ask`, `artifact`) reach the
runner and the bare `blizzard` binary resolves to the deployment's venv; the rest of your shell stays untouched and
nothing more leaves the daemon. The exec reasserts `harness_permission_mode` from `blizzard-runner.toml` — scaffold
default `bypassPermissions`, meaning per-tool approval prompts are disabled exactly as for the daemon-spawned worker;
set another mode, or empty to omit the flag, if your deployment wants attended sessions prompted.

What authorizes the session's verbs is the open takeover fact itself, not a fresh lease: the reference lease it names is
very often already closed — the ordinary shape for a parked or escalated chunk — and the daemon resolves a worker verb's
lease as that lease's own activeness or an open takeover naming it, so the verbs work against the same closed lease
record, unchanged in id, node, and epoch. A taken-over session installs no heartbeat or session-end hooks: quitting it
must not record a done-signal against the lease, so liveness reporting stays a daemon-spawned-worker concern.

A takeover ordinarily ends when you exit and the CLI's own cleanup PATCHes it closed, but the hub can end it too: a
chunk reaching a terminal status while a takeover is open has the takeover fact closed by PULL on its next tick; the
end-PATCH is idempotent, so a session stopped from the board mid-takeover still exits cleanly rather than erroring.

takeover checks the runner's actual held session state, never the escalation's composed commands, so it can succeed
against an escalation carrying neither; it refuses with `ChunkNotTakeable` when this runner does not hold the chunk, no
resumable session sits behind its most recent lease, or a takeover is already open.

For a runner-composed escalation the takeover verb, not the escalation record's raw resume string, is the supported way
in: `blizzard runner status` still prints the raw string deliberately unchanged, and the board renders the wrapped verb
as the primary copyable command, demoting the raw string to a collapsed unwrapped-fallback disclosure present only when
the escalation carries one. The raw string resumes the transcript but carries neither the permission mode nor the
identity env: pasted into a bare terminal it runs at the harness's interactive permission default, and its
`blizzard runner` verbs cannot reach the runner — it can only read and edit.

## Resolving an escalation

Which commands a given escalation carries, and whether its session is still reachable through takeover at all, is a
domain fact owned by the Escalation section of blizzard-context's
[domain/humans.md](https://github.com/paul-gross/blizzard-context/blob/master/domain/humans.md). Only when no runner can
enter the session does resolving an escalation mean acting on the chunk directly — reading its bounce history or
migration guidance — and requeuing; when the work was finished outside the fleet entirely, stop the chunk instead, which
closes the escalation with it ([control-verbs.md](./control-verbs.md)).
