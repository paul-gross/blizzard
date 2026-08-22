# Operating chunks and graphs

## Taking over a parked session — `blizzard runner takeover`

`blizzard runner takeover <chunk_id>` continues a parked chunk's worker session interactively, in your own terminal. It
records a takeover fact with the daemon first — so no loop step can respawn or judge the session while you hold it —
then execs the harness's resume command as your terminal's child, and marks the takeover ended when you exit (even on
Ctrl-C). Run it as the service account, like every socket verb — see [The runner's two doors](./runner-doors.md) for
what that means and the `--dir`/`--runner-url` transport it addresses the daemon over.

Two things ride that exec which a plain copy-paste of a resume command does not get:

- **The configured permission mode.** The exec'd command reasserts `harness_permission_mode` from `blizzard-runner.toml`
  — whose scaffold default is `bypassPermissions`, meaning the session runs with **per-tool approval prompts disabled**,
  exactly as the daemon-spawned worker did. Set the knob to another mode (or empty, to omit the flag) if your deployment
  wants attended sessions prompted.
- **The lease's identity env.** The daemon returns a bounded set — the `BLIZZARD_*` identity vars plus its own `PATH`
  and `HOME` — which the verb layers over your terminal's environment, so the session's `blizzard runner` verbs
  (`attach`, `ask`, `artifact …`) reach the runner and the bare `blizzard` binary resolves to the deployment's venv.
  Opening a takeover **mints a fresh lease capability token** (invalidating the previous one); everything else about
  your shell — `TERM`, locale, your own variables — stays untouched, and nothing beyond that bounded set leaves the
  daemon. What actually authorizes those verbs is the **open takeover fact itself** (issue #291), not a fresh lease: the
  reference lease it names is very often already closed — the ordinary shape for a parked or escalated chunk — and the
  daemon resolves a worker verb's lease as that lease's own activeness *or* an open takeover naming it, so the session's
  verbs reach the runner against the same closed lease record the parked attempt held, unchanged in id, node and epoch.

For a **runner-composed** escalation, this makes the takeover verb, not the escalation record's raw string, the
supported way in. `blizzard runner status` still prints that raw string (`cd … && claude --resume …`) — that surface is
deliberately unchanged — and the board (issue #251) now renders the wrapped verb as the primary, copyable command, with
the raw string demoted to a collapsed "Unwrapped fallback" disclosure below it, present only when the escalation carries
one. Either way, the raw string resumes the transcript but deliberately carries **neither** of the above: pasted into a
bare terminal it runs at the harness's interactive permission default, with no identity env — that session can read and
edit, but its `blizzard runner` verbs cannot reach the runner.

Which command(s) a given escalation carries, and whether the underlying session is still reachable through the takeover
verb at all, is a domain fact governed by
[`blizzard-context`'s `domain/humans.md`](https://github.com/paul-gross/blizzard-context/blob/master/domain/humans.md)
§Escalation, not a deployment one — read there for which case produces which shape. Operationally, the one thing worth
knowing here: `blizzard runner takeover` checks the runner's actual held session state, never the escalation's own
composed commands, so it can succeed even against an escalation carrying neither. It refuses with `ChunkNotTakeable`
when that check fails — this runner does not hold the chunk, no resumable session sits behind its most recent lease, or
a takeover is already open — so on a split deployment run the verb on the runner's own host first: the wrong host
refuses with the not-held message even while the session is alive elsewhere. Only when no runner can enter the session
does resolving the escalation mean acting on the chunk directly (reading its bounce history or migration guidance) and
requeuing, not taking anything over — or, when the work has been finished outside the fleet entirely, stopping the
chunk, which closes the escalation with it (see [the `stop` verb](./control-verbs.md)).

A taken-over session also installs **no** heartbeat or session-end hooks: quitting it must not record a done-signal
against the lease, so liveness reporting stays a daemon-spawned-worker concern.

Ending the takeover ordinarily happens the same way it opened — a person exits the session and the CLI's own `finally`
PATCHes it closed — but the hub itself can end it too (issue #291): if the chunk transitions to a terminal status while
a takeover is still open, `PULL` closes the takeover fact on its own next tick, the same way it already mirrors an
escalation's own hub-side close. The end-PATCH is idempotent, so a session stopped from the board mid-takeover still
exits cleanly when its own `finally` reaches an already-closed takeover — it does not surface as a "could not reach the
runner" error.

## Editing an unclaimed chunk's build config

While a chunk sits **unclaimed** — resting `not_ready` (minted but not yet promoted) or promoted to `ready` with no
runner holding it yet — its pinned **graph** and its **default model/effort** are editable via `PATCH /api/chunks/{id}`
(below). Issue #120 widened this past its original `not_ready`-only window (issue #27): the wrong graph is often noticed
only after promote, with no runner anywhere near the chunk yet. Once the chunk is **claimed or later** — `running`,
`delivering`, `waiting_on_human`, `needs_human`, `paused` (post-claim), `done`, or `stopped` — these edits are refused
with `409`.

The **graph** carries one further condition the defaults do not (issue #271): the chunk must also **never have moved**.
Unclaimed and never-moved are not the same test. A chunk that was claimed, ran a node, and was then detached (see
[detaching a chunk](./control-verbs.md)) derives `ready` again while still standing on a node of the graph it is pinned
to, and re-pinning it in place would leave its current node absent from the new graph — so that edit is refused `409`
even though the chunk is unclaimed. Moving a chunk that has run is not an edit's job, and which verb takes it depends on
what is actually changing: **which graph** the chunk is on, at its next transition, is `chunk migrate` below
(`--to-graph` is required, and a target equal to the chunk's own current pin is refused `409`); **where on that graph**
it stands, or which graph it is on right now, is [`chunk restart`](./control-verbs.md). The defaults name no node to be
stranded on, so they stay editable for as long as the chunk is unclaimed.

`PATCH /api/chunks/{id}` (issue #124) applies any of `graph_id`, `default_model`, `default_effort`, and
`intended_migration` in one request, all-or-nothing: if any supplied field is outside *its own* editable window, the
whole request is refused (`409` — naming the field, except the already-moved refusal, which names the chunk and points
at migration) and nothing in the body is applied. The two defaults take the unclaimed window above and `graph_id` that
window plus never-moved; `intended_migration` — see "Migrating a claimed chunk to another graph" below — is different:
it is editable at **any non-terminal status**, claimed or not, so a `PATCH` naming it alongside a claimed chunk's
now-sealed `graph_id` still refuses the whole request on `graph_id`.

**The two defaults** (issue #144) are what a surface declaring neither inherits: effective precedence is a graph
`sessions:` declaration > the chunk default > the runner's own default. `default_model` is a **prioritized preference
list** in the same vocabulary a session declaration uses — a `blizzard:` tier alias or a harness-native model name,
resolved left-to-right at session mint; `default_effort` is a single value. Neither vocabulary is validated hub-side:
the alias tables live in each runner's own config, so both are opaque preference strings here. A blank entry is `422`;
an empty list and an explicit `null` effort are real values — *express no preference*, the state ingest mints — not
"leave unchanged".

From the CLI, `--default-model` is repeatable and **ordered**:

```text
blizzard hub chunk set ch_… --default-model blizzard:advanced --default-model blizzard:basic \
  --default-effort high
blizzard hub chunk show ch_…     # reads both back
```

There is deliberately **no web editing surface** for either — the chunk detail dock's model editor was removed with
`Chunk.model`, and is not replaced. `chunk show` (or the detail payload's `default_model`/`default_effort` fields) is
the read-back.

A graph edit has two further distinct `409`s beyond the status window. Targeting a graph that has been **retired** (see
"Graph lifecycle — retire and re-enable" below) is refused even on an otherwise-editable chunk, naming the retired graph
id rather than the chunk's status. Editing a chunk that has **already moved** is refused as above. The already-moved
check runs **first**, so a moved-but-unclaimed chunk aimed at a retired graph reports the move, not the retirement.

## Migrating a claimed chunk to another graph

`blizzard hub chunk migrate <chunk-id> --to-graph <graph> [--node <name>] [--cancel]`, or `PATCH /api/chunks/{id}`
`intended_migration` (issue #124) — sets a **standing intent** to move a chunk onto another graph, consulted (never
applied eagerly) at the chunk's *next* transition. Unlike the [stop-work verbs](./control-verbs.md), it does not stop or
interrupt any in-flight work: the current attempt runs to its normal verdict, and only that transition either fires the
intent or, for `auto` mode with no name match, leaves it set for the transition after — `chunk restart --to-graph` above
is the eager counterpart, for when there is no next transition worth waiting for. `--to-graph` names a graph id or a
graph name resolved to the newest enabled graph of that name; a blank name is refused (`422`), and a retired target or a
target equal to the chunk's own current pin is refused (`409`). With no `--node`, the intent is `auto`: it fires only
when the transition's own destination node name also exists on the target graph, landing there; with no name match the
transition applies unchanged and the intent stays set for next time. `--node <name>` makes it `forced`: it fires
unconditionally at the next transition, landing on the named node regardless of the transition's own destination —
refused (`409`) up front if that node does not exist on the target graph. `--cancel` (or `PATCH` with
`intended_migration:
null`) clears a standing intent without firing it.

Editable at **any non-terminal status** — `not_ready` and `ready` too, not just once claimed — since the intent is a
plain mutable chunk property, not a transition itself; it is only ever *consulted* at a transition, which is why in
practice it matters once a chunk is claimed and progressing, and why it complements rather than replaces the never-moved
graph repin above — it is the only way to move a chunk that has run. When the intent fires, the chunk's movement is
recorded as a migration exactly like an authored cross-graph judgement choice (see "Graph lifecycle" below): it re-pins
the chunk's graph, lands it on the resolved node, and clears the intent in the same write. Landing governs by the landed
node's own executor — a migration landing on a hub-executed node derives `delivering`, exactly as a transition into one
does. See `blizzard hub chunk migrate --help` for the CLI's full contract.

## Following the latest mint automatically

The intent above is per chunk and aims at one resolved mint **id** — deliberately, so a later mint under the same name
never silently redirects a chunk an operator aimed by hand. The cost is that every workflow edit strands the fleet on
old mints until each in-flight chunk is migrated individually. `follow_latest` (issue #164) is the standing policy that
removes that chore: a chunk pinned to a follow-latest graph re-pins to the newest *enabled* mint of its own graph's
**name** at its next transition.

Two levels, and the graph wins where it speaks:

| Where                                                              | Value                              | Meaning                                                                            |
| ------------------------------------------------------------------ | ---------------------------------- | ---------------------------------------------------------------------------------- |
| `follow_latest` in `blizzard-hub.toml`                             | `true` / `false` (default `false`) | the fleet-wide default for every graph that says nothing                           |
| `blizzard hub graph follow-latest <graph-id> true\|false\|inherit` | `true` / `false` / `null`          | this mint's own override; `inherit` (the default for every mint) defers to the hub |

The shipped default is `false`, so landing this changed nothing until someone opts in. The policy is set **per mint**,
not per name: a chunk consults the policy of the graph it is pinned to, so arming a lineage means arming the mint its
chunks sit on — or setting the hub default, which covers every name at once. `GET /api/graphs/{id}` serves the stored
tri-state as-is, so a reader can tell "this graph says nothing" from "this graph says false".

It rides the same deferred path as an explicit intent, with the same guarantees: nothing in-flight is interrupted, the
move is recorded as a **migration** fact rather than disguised as a transition, and it fires only at a transition the
chunk was making anyway. Landing is name-match-else-entry on that transition's own destination — the chunk goes where it
was already going, just on the newer mint, falling back to the target's entry node when the newer definition no longer
has that node at all.

An explicit `intended_migration` **outranks** the policy: if a chunk carries one, the policy is not consulted at all,
including on a transition where an `auto` intent falls through for want of a name match. The policy is otherwise a plain
no-op — no error, no fact — when the chunk is already on the newest mint, when every newer mint is retired, or when the
effective policy resolves `false`. It will never move a chunk *backwards*: if a chunk's own mint has been retired so
name resolution answers with an older one, the chunk stays where it is.

## Graph lifecycle — retire and re-enable

`blizzard hub graph list` / `graph retire <graph_id>` / `graph enable <graph_id>` (issue #101), or the graph explorer's
own **Retire** / **Re-enable** buttons and lifecycle badge in the web board — an operator's brake over which graph a
**name** resolves to. Not a work-stopping lever like the [chunk control verbs](./control-verbs.md): a graph carries no
chunk, no claim, no live worker to interrupt. Retiring never touches the graph's own immutable row — it appends a
`graph.retired` fact, reversed by `graph enable`'s `graph.enabled` fact — so the brake is **reversible**, and every
toggle is itself an append-only audit trail rather than a destructive edit.

**What retiring changes, and what it deliberately leaves alone.** A chunk that already pins a retired graph keeps
running it to completion — existing pins are left to run out; issue #101 is scoped to blocking only *new* resolution by
name, never touching a chunk mid-workflow. What a retire blocks is every name lookup: the default-graph pin at ingest
and a cross-graph migration's `graph:<name>` judgement target both resolve through the newest **non-retired** graph of
that name, skipping every retired `graph_id` entirely.

**Retiring every version of a name is a real trap, not a hypothetical.** If every graph ever minted under one name —
including the packaged `default-delivery` the hub ingests against out of the box — is retired, name resolution has
nothing left to hand back. The next ingest that would otherwise lazily mint a fresh copy of the packaged default
**refuses with `503`** instead: minting a fresh copy there would be immediately effective and would silently undo the
retire the moment it landed, including across a hub restart. Re-enable one of the retired versions, or mint a new graph
under that name, to clear it. A cross-graph migration choice naming an all-retired target has the same "nothing left to
resolve" shape at the moment a chunk takes it.
