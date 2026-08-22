# Chunk and runner control verbs

Seven verbs stop, re-aim, or settle work, and two of them share the word "pause," which is exactly where operators mix
them up. The five chunk-level verbs split along what they do to the claim: keep it (`chunk pause`, `chunk restart`),
give it away (`detach`), or end it for good, as either an abandonment (`stop`) or a hand-completion (`chunk done`, issue
#294).

"Restart" is the section's other overloaded word, and the two senses are unrelated. `chunk restart` below is this
operator verb, aimed at one chunk. **Daemon restart** — stopping and starting the runner process itself, and the
*restart-resume* that re-attaches its in-flight sessions afterward (issues #12, #13) — moves no chunk anywhere and is
never issued at a chunk; it is the ordinary `systemctl restart` of a service, covered under
[The runner's two doors](./runner-doors.md).

- **`blizzard hub chunk pause <chunk_id>` / `chunk resume <chunk_id>`** (issue #46), or the board's **Pause**/**Resume**
  control in the chunk detail dock beside Detach — targets **one chunk**. On a chunk with a live claim, the runner kills
  that chunk's live worker but **keeps the claim**: the lease, route, epoch, held environments, and retry budget all
  survive untouched — only the process dies. Pause is also allowed on a chunk that hasn't been claimed yet (`ready`):
  there it holds the chunk out of the queue instead — it derives `paused`, not `ready`, so FILL skips it until it's
  resumed. `chunk resume` respawns a parked session **in place**, under the unchanged lease/epoch/session id, consuming
  no retry (a still-unclaimed chunk just re-derives `ready` and rejoins the queue). Refused (`409`) on a chunk that is
  `done`/`stopped`/`delivering`; deliberately **allowed** on `waiting_on_human`/`needs_human` — pause is a broad lever.
  (The `stopped` case in that refusal list — see below — was inert until `stop` existed to reach it.) The pause *fact*
  survives the answer to that question untouched (answering never un-pauses a chunk), but the *derived status* doesn't
  show `paused` while the question is open — a chunk both paused and parked on a question derives `waiting_on_human`
  first, so the board shows a `waiting_on_human` chip, not `paused`, until the question is answered. The dock still says
  so plainly and still offers **Resume** there — it reads the pause fact (`ChunkDetail.pause`), not the chip. Once
  answered, the pause fact is still there, so the chunk then derives `paused` (and stays parked) rather than resuming —
  `chunk resume` is what actually lets it go. `chunk resume` is idempotent — resuming an already-running chunk is a
  harmless no-op.
- **`blizzard hub chunk detach <chunk_id>`**, or the board's **Detach** control in the chunk detail dock (issue #42) —
  also targets **one chunk**, but the opposite direction: it **gives the claim away**. Both doors reach the same
  `POST /api/chunks/{id}/detach`, so either does exactly the same thing. The route is released, every held environment
  is freed, the lease closes, and the chunk re-derives `ready` so any runner — including a different one — can claim it
  next. Any live worker is abandoned along with everything else, not merely killed-and-kept. It is **not** requeue: no
  supersession fact is recorded and no epoch bumps, so a `needs_human` chunk detached this way is still `needs_human`
  afterward — only the route is gone. Refused (`409`) when the chunk has no live route left to release. See
  `blizzard hub chunk detach --help` for the CLI's full contract.
- **`blizzard hub chunk stop <chunk_id>`** (issue #118) — CLI/API only, with no board control today; there is no Stop
  button in the chunk detail dock the way Pause, Detach, and now Complete each have one, only
  `POST /api/chunks/{id}/stop`. The `chunk.stopped` fact is **irreversible** — there is no `un-stop` — but it is no
  longer guaranteed the last word on the chunk: an operator can still complete it afterward (see `chunk done` below),
  and the derived status then reads `done`, not `stopped`. It does **both** of what `chunk pause` and `detach` each do
  half of: it writes the `chunk.stopped` fact *and* releases any live route, so the holding runner frees the
  environments on its own next tick — no separate `detach` call needed. Unlike `detach`, a live route is not required:
  stop is allowed on `not_ready`, `ready`, and an already-detached chunk alike — the route release is conditional, not
  required. Stopping an escalated chunk also **closes its escalation** (issue #292; reaching `done` does the same): the
  chunk leaves the critical [`needs-human` feed](./observability.md) and the holding runner drops it from
  `blizzard runner status` and its panel on the next PULL — so the composed resume command for the parked session goes
  with it, which is worth knowing before you reach for it, since there is no un-stop. Refused (`409`) only when the
  chunk is already `done` or `stopped` — not retroactive un-delivery, and not a lever for clearing a
  `delivering`/`waiting_on_human`/`needs_human` chunk back to a fresh state, only for ending it. See
  `blizzard hub chunk stop --help` for the CLI's full contract.
- **`blizzard hub chunk done <chunk_id>`**, or the board's **Complete** control in the chunk detail dock (issue #294) —
  a pure client of `POST /api/chunks/{id}/complete`, gated by `CHUNK_CONTROL` like every other control verb here. It
  writes its own `chunk.completed` fact — a hand-completion, not a synthetic reading of some other fact — reachable from
  **any** non-`done` status, including `stopped`: unlike `stop`, `chunk done` has no un-verb of its own either, but it
  is not foreclosed by having been stopped first. Between a `chunk.stopped` and a `chunk.completed` fact on the same
  chunk, the derived status favors whichever was recorded **later** (a tie going to the completion), so a chunk stopped
  and then hand-completed reads `done` afterward, not `stopped`. Releases any live route and held hub-exec slot in the
  same store transaction as the fact write, exactly as `stop` does, and its work-item refs become eligible for closure
  alongside a landed repo (`closable_work_refs`) — completing a chunk by hand closes out its issue the same way landing
  its repos would. **Idempotent, not refused**: completing an already-`done` chunk writes no second fact and is not a
  409 — deliberately asymmetric with `stop`, which stays refused on a `done` or `stopped` chunk. See
  `blizzard hub chunk done --help` for the CLI's full contract.

  **`stop` is not how a chunk reaches `done` — `chunk done` and the graph both are, and now either can follow a stop.**
  `stopped` records that an operator ended the chunk without it having delivered; `done` records that the chunk
  finished, either because the graph reached its reserved terminal (`to: done`, in the shipped graphs the
  `retrospective` node's `recorded` choice at the end of `deliver` → `retrospective`) or because an operator
  hand-completed it with `chunk done`. Unlike the graph path, `chunk done` needs no graph cooperation: it is a pure
  operator write, exactly like `stop`, and it is available *after* a stop as well as before one. So a chunk whose work
  you landed by hand, outside the fleet, no longer has to end at `stopped` as its truthful final record — stopping it
  and then running `chunk done` (or the board's Complete) marks it `done` instead, once you have confirmed the work
  actually landed. What remains foreclosed is going the other way: there is no `un-stop` and no `un-complete`, so once a
  chunk reads `done` — by either path — it stays there.
- **`blizzard hub chunk restart <chunk_id> [--to-graph <graph>] [--node <name>]`** (issues #370, #371) — CLI/API only, a
  pure client of `POST /api/chunks/{id}/restart`. It forces the chunk onto a node **now**, on a freshly minted session:
  the hub records the move at a bumped epoch, so the holding runner tears the running attempt down on its next tick and
  re-enters the named node with clean context. `--node` defaults to the chunk's current node, which is the common case —
  restart this step, the worker is thrashing; on a chunk that has never moved, that default is the entry node of
  whichever graph it lands on. The claim is **kept**: route, tenure and held environments all survive, so the re-entry
  lands in the same worktree with the work already on disk, the artifacts the superseded step produced stay readable,
  and — like a pause, and unlike a failure — **no retry is consumed**, so restarting a thrashing step over and over
  never escalates the chunk you are rescuing. The bumped epoch is the guarantee, not the kill: a completion the
  displaced worker submits afterward is rejected as stale rather than advancing the chunk. Whatever parked the chunk
  goes with the move — an open ask is answered with a fixed system answer, an open gate decision is closed, an open
  escalation superseded — so nothing is left to re-park it at a node it is no longer on. Like `stop` and unlike
  `detach`, a live route is not required: an unclaimed `ready` or `not_ready` chunk moves too, re-entering the queue at
  the target node, and the next claim's envelope is what carries the move to whoever picks it up. Refused (`409`) when
  the chunk is `done`/`stopped`, when `--node` names no node on the graph the move lands on, or when — with no node
  named — the chunk stands on a node that graph no longer carries: the position is refused, never silently rewound to
  the entry node. See `blizzard hub chunk restart --help` for the CLI's full contract.

  **`--to-graph` moves the chunk onto another graph in the same breath** (issue #371). The move then records **two**
  facts in one store write — a migration for the cross-graph re-pin, so re-pinning stays migration's job, and the
  restart for the forced clean re-entry — plus, in that same write, any standing `intended_migration` the chunk was
  carrying, which an eager move supersedes rather than leaving parked to fire later. `--node` then names a node on the
  **target** graph; omitted, the chunk's current node name is matched onto the target the way an `auto` migration's
  landing is, and a failed match is refused (`409`) rather than rewound to the target's entry node. The one chunk that
  does land on the target's entry is one that has never moved at all — it stands on nowhere, so the entry is where it
  would have started. The re-entry is stamped by the **target** graph's declarations — its `sessions:` model, effort and
  compaction window — which is the whole point: a chunk thrashing under a stale window is moved onto the fixed mint and
  re-enters under it immediately, with no node-step run to manufacture a transition. A target graph that is unknown
  (`404`) — a name whose every mint is retired reads as unknown here — retired and named by id (`409`), or equal to the
  chunk's own current pin (`409`, use plain `restart`) is refused, and every refusal writes nothing.

  **Restarting an escalated chunk clears the hub-side row, and the runner's own list lags.** The move supersedes the
  escalation, so the chunk leaves the critical `needs-human` feed immediately — but the runner that raised it keeps
  listing it in `blizzard runner status` and its panel until something terminal happens to the chunk, exactly the lag
  the `stop` bullet above describes for itself.

  **Which brake outranks it.** The **per-chunk** `chunk pause` above does: a paused chunk parks as usual and honors the
  move on the tick after the pause lifts. Of the two **runner-level** brakes, the hub's does nothing to a restart at all
  — it gates new claims only, so the move lands and the holding runner still tears down and re-enters. The runner's own
  local brake defers the whole teardown: no worker is killed and nothing re-enters while it is on, because the re-entry
  is a spawn and the local brake starts no processes. The move stays recorded and the first tick past the brake honors
  it.

  **`restart` is not `migrate`.** `chunk migrate` records a **standing intent** — inspectable in `chunk show`,
  overwritable, `--cancel`able, and consulted only at the chunk's next transition, so nothing in flight is interrupted
  and the chunk moves whenever it next completes a node-step. `chunk restart` performs an **event** that has already
  happened when the call returns. Both can cross graphs; they differ in *when*, and `restart --to-graph` is the one that
  does not wait for a transition the operator would otherwise have to manufacture.
- **`blizzard hub runner pause <runner_id>` / `runner resume <runner_id>`** (the hub brake) and **`runner pause` /
  `runner start`**, or the runner panel's own Pause/Resume control (the runner's own local brake, issue #45 and issue

  #133 — see [The runner's two doors](./runner-doors.md)), are **per-runner**, not per-chunk. Neither kills any
  particular chunk's worker: the hub brake only stops that runner from claiming *new* work (every in-flight chunk, live
  worker included, runs on); the local brake additionally blocks every other spawn site (restart-resume, an
  answer-resume, a requeue respawn, …) but still never kills a worker that is already running — pausing locally is not a
  drain.

The distinction worth holding onto: `chunk pause` and `chunk restart` are the two chunk-level verbs that kill a live
worker while **keeping** the claim, and they differ in what they keep of the attempt — pause keeps the lease, epoch and
session so the resume lands in place, restart discards all three so the re-entry starts clean. `detach`, `stop`, and
`chunk done` all give the claim away (or end it), differing in whether the chunk can be reclaimed afterward and whether
it ends as `stopped` or `done`. The two runner-level brakes sit apart from all five: they never touch a live worker, and
they have no notion of "this one chunk" at all.

**A pause-parked chunk still occupies an agent slot.** FILL only ever claims new work into a runner's *open* slots, and
a chunk pause deliberately leaves the lease active and its environments held warm for the resume — that is what makes
the resume land in place instead of re-provisioning. So a paused lease counts against `max_agents` exactly like a
running one, with no worker consuming it. Pause enough chunks on one runner and it silently stops claiming new work — no
error, nothing beyond the pause's own log line — because every slot is spoken for by parked claims. Detach, stop, and
`chunk done`, by contrast, each free the slot immediately (the claim is given away, or ended, not held).

A restart into a **standing** chunk pause does not resume it — the runner checks the pause fact first, ahead of the
normal restart-resume path described in [The recovery contract](./recovery.md), so a chunk still marked paused when the
runner comes back is (re-)parked, not respawned. The claim is kept exactly as it would be if the pause had landed on a
live tick; only a chunk that was *not* paused resumes in place on restart.
