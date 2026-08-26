# Chunk control verbs

The chunk-level control verbs split by what they do to the claim: `chunk pause` and `chunk restart` keep it, `detach`
gives it away, and `stop` and `chunk done` end it for good — `stop` as an abandonment, `chunk done` as a
hand-completion. `detach`, `stop`, and `chunk done` all give the claim away or end it, differing in whether the chunk
can be reclaimed afterward and whether it ends `stopped` or `done`. `chunk delete` sits on none of the three: it is
gated by the chunk's status rather than by what happens to a claim, reachable only at `not_ready` or unclaimed `ready` —
a chunk in that unacquired set holds no claim to keep, give away, or end in the first place.

Every chunk control verb's full CLI contract lives in its own `blizzard hub chunk <verb> --help`; the facts here are the
cross-verb distinctions the help text does not draw.

## Pause and resume

`chunk pause`, or the board's Pause control in the chunk detail dock, targets one chunk: on a live claim the runner
kills that chunk's worker but keeps the claim — lease, route, epoch, held environments, and retry budget all survive;
only the process dies. Pause is also allowed on a still-unclaimed `ready` chunk, where it holds the chunk out of the
queue: the chunk derives `paused` and FILL skips it until resumed. Pause is refused (409) on a `done`, `stopped`, or
`delivering` chunk, and deliberately allowed on `waiting_on_human` and `needs_human` — it is a broad lever.

A pause-parked chunk still occupies an agent slot: FILL claims only into open slots, and a `chunk pause` deliberately
keeps the lease active with environments held warm for the in-place resume, so a paused lease counts against
`max_agents` exactly like a running one — pause enough chunks on one runner and it silently stops claiming new work,
with nothing beyond the pause's own log line saying why; `detach`, `stop`, and `done` each free the slot immediately.

A chunk both paused and parked on an open question derives `waiting_on_human` first, so the board chip reads
`waiting_on_human`, not `paused`, until the question is answered — but the pause fact survives (answering never
un-pauses), so once answered the chunk derives `paused` and stays parked; the detail dock reads the pause fact itself
and offers Resume throughout, and `chunk resume` is what actually lets the chunk go.

`chunk resume` respawns a parked session in place under the unchanged lease, epoch, and session id, consuming no retry;
a still-unclaimed chunk just re-derives `ready` and rejoins the queue; resuming an already-running chunk is a harmless
no-op.

## Detach

`chunk detach`, or the board's Detach control — both reach `POST /api/chunks/{id}/detach` — gives the claim away: the
route is released, every held environment freed, the lease closed, and the chunk re-derives `ready` for any runner,
including a different one, to claim next; a live worker is abandoned along with everything else, not merely
killed-and-kept.

Detach is not requeue: no supersession fact is recorded and no epoch bumps, so a `needs_human` chunk detached this way
is still `needs_human` afterward — only the route is gone; detach is refused (409) when there is no live route left to
release.

## Restart

`chunk restart [--to-graph <graph>] [--node <name>]` is CLI/API only (`POST /api/chunks/{id}/restart`): it forces the
chunk onto a node now, on a freshly minted session — the hub records the move at a bumped epoch, and the holding runner
tears the running attempt down on its next tick and re-enters the named node with clean context. The bumped epoch, not
the kill, is restart's guarantee: a completion the displaced worker submits afterward is rejected as stale rather than
advancing the chunk.

pause and restart are the two verbs that kill a live worker while keeping the claim, differing in what survives of the
attempt: pause keeps lease, epoch, and session so the resume lands in place; restart discards all three so the re-entry
starts clean. restart keeps the claim: route, tenure, and held environments survive, so the re-entry lands in the same
worktree with the work on disk and the superseded step's artifacts readable — and like pause, no retry is consumed, so
restarting a thrashing step repeatedly never escalates the chunk being rescued.

The re-entry is stamped by the target graph's declarations — its `sessions:` model, effort, and compaction window —
which is the point: a chunk thrashing under a stale window moves onto the fixed mint and re-enters under it immediately,
with no node-step run to manufacture a transition. Whatever parked the chunk goes with a restart: an open ask is
answered with a fixed system answer, an open gate decision closed, an open escalation superseded — nothing is left to
re-park it at a node it no longer occupies.

`--node` defaults to the chunk's current node — the common case, restarting a thrashing step; on a chunk that has never
moved, the default is the entry node of whichever graph the move lands on. Like stop and unlike detach, restart needs no
live route: an unclaimed `ready` or `not_ready` chunk moves too, re-entering the queue at the target node, the next
claim's envelope carrying the move.

restart is refused (409) when the chunk is `done` or `stopped`, when `--node` names no node on the graph the move lands
on, or when — with no node named — the chunk stands on a node that graph no longer carries: the position is refused,
never silently rewound to the entry node.

`--to-graph` moves the chunk onto another graph in the same breath, recording two facts in one store write — a migration
for the cross-graph re-pin (re-pinning stays migration's job) and the restart for the forced clean re-entry — plus
superseding, in that same write, any standing `intended_migration` the chunk carried. With `--to-graph`, `--node` names
a node on the target graph; omitted, the chunk's current node name is matched onto the target the way an auto migration
lands, and a failed match is refused (409) rather than rewound to the target's entry — only a chunk that has never moved
lands on the entry, since it stands on nowhere. A restart target that is unknown (404 — a name whose every mint is
retired reads unknown), retired-and-named-by-id (409), or equal to the chunk's current pin (409 — use plain restart) is
refused, and every refusal writes nothing.

restart is not migrate: `chunk migrate` ([chunk-operations/migration.md](./chunk-operations/migration.md)) records a
standing intent — inspectable in `chunk show`, overwritable, cancelable, consulted only at the chunk's next transition,
interrupting nothing in flight — while `chunk restart` performs an event already done when the call returns; both cross
graphs, they differ in when, and `restart --to-graph` is the one that does not wait for a transition the operator would
have to manufacture.

A restart into a standing `chunk pause` does not resume it: the runner checks the pause fact ahead of the restart-resume
path ([recovery.md](./recovery.md)), so a chunk still paused when the runner comes back is re-parked, not respawned —
the claim kept exactly as if the pause had landed on a live tick. Of the brakes, only the per-chunk pause outranks a
restart: a paused chunk parks and honors the move on the tick after the pause lifts; the hub's runner brake does nothing
to a restart (it gates new claims only, so the teardown and re-entry proceed); the runner's local brake defers the whole
teardown — the re-entry is a spawn and the local brake starts no processes — with the first tick past the brake honoring
the recorded move.

Restarting an escalated chunk clears the hub-side row immediately — the chunk leaves the needs-human feed — but the
runner that raised it keeps listing it in `runner status` and its panel until something terminal happens to the chunk,
the same lag stop has.

"Restart" has two unrelated senses: `chunk restart` is the operator verb aimed at one chunk, while a daemon restart —
`systemctl restart` of the runner process, and the restart-resume that re-attaches its in-flight sessions — moves no
chunk anywhere and is never issued at a chunk ([runner-doors.md](./runner-doors.md) owns it).

## Stop and done

`stopped` records that an operator ended the chunk without it delivering; `done` records that it finished — either the
graph reached its reserved terminal (`to: done`; in the shipped graphs, the retrospective node's recorded choice) or an
operator hand-completed it, no graph cooperation needed.

`chunk stop` is CLI/API only (`POST /api/chunks/{id}/stop`) — no board control, unlike Pause, Detach, and Complete. stop
is refused (409) only on a chunk already `done` or `stopped`; it is not retroactive un-delivery and not a lever for
clearing a `delivering`, `waiting_on_human`, or `needs_human` chunk back to fresh — only for ending it.

stop does both halves of what pause and detach each do half of: it writes the `chunk.stopped` fact and releases any live
route, the holding runner freeing the environments on its next tick — no separate detach needed; unlike detach, a live
route is not required, so stop is allowed on `not_ready`, `ready`, and already-detached chunks alike.

Stopping an escalated chunk closes its escalation (reaching `done` does the same): the chunk leaves the critical
needs-human feed and the holding runner drops it from `blizzard runner status` and its panel on the next PULL — taking
the composed resume command for the parked session with it, worth knowing before you stop.

`chunk done`, or the board's Complete control (`POST /api/chunks/{id}/complete`, gated by `CHUNK_CONTROL` like every
control verb here), writes its own `chunk.completed` fact — a hand-completion, not a synthetic reading of another fact —
reachable from any non-done status, including `stopped`. It is idempotent, not refused: completing an already-done chunk
writes no second fact and is not a 409 — deliberately asymmetric with stop, which stays refused on a `done` or `stopped`
chunk.

`chunk done` releases any live route and held hub-exec slot in the same store transaction as the fact write, exactly as
stop does, and enqueues a close intent per still-open work-item ref in that same transaction (`_enqueue_close_intents`)
— hand-completing closes out the chunk's issue the way landing would, once the drain sweep retires it.

The `chunk.stopped` fact is irreversible — there is no un-stop — but no longer the guaranteed last word: an operator can
still complete the chunk afterward, and the derived status then reads `done`. Between a `chunk.stopped` and a
`chunk.completed` fact on one chunk, the derived status favors whichever was recorded later, a tie going to the
completion — so a chunk stopped and then hand-completed reads `done`. Work landed by hand outside the fleet no longer
has to end at `stopped`: stop the chunk, confirm the work landed, then `chunk done` marks it done — but there is no
un-stop and no un-complete, so a chunk reading `done` by either path stays there.

## Delete

`chunk delete <chunk_id>` (`blizzard hub chunk delete <chunk_id> [--by] [--yes]`), the board's confirmed Delete control
beside Promote on any `not_ready` or `ready` chunk's card, or `DELETE /api/chunks/{chunk_id}` (gated by `CHUNK_CONTROL`
like every control verb here) — deletes a chunk gated on the same unacquired predicate `chunk group` requires of every
chunk it folds away: `not_ready` or unclaimed `ready`.

Delete is refused (409) at every other status, `paused` included. This is a status gate, not a claim-liveness one, so it
differs from pause or stop's own guards: delete never asks whether a runner holds the chunk, only whether the chunk's
own status sits in that unacquired set — a still-unclaimed chunk that has been paused is refused all the same, on status
alone.

A hub item and its chunk live and die together: deleting a chunk withdraws every open `hub:`-source pointer it holds —
any `forge:`-source pointer on the same chunk survives untouched — and withdrawing a hub item deletes its unacquired
holder chunk in the same stroke rather than refusing the withdrawal ([work-sources.md](./work-sources.md) owns the
withdrawal route's own guard). A chunk a runner still holds still refuses the withdrawal exactly as before.

Delete is irreversible — there is no un-delete — and, like stop, needs no live route: an unclaimed `not_ready` or
`ready` chunk has none to release.

## Runner-level brakes

The two runner-level brakes — the hub brake (`blizzard hub runner pause`/`resume`) and the runner's own local brake
(`runner pause`/`start`, or the runner panel's control; [runner-doors.md](./runner-doors.md)) — are per-runner, not
per-chunk, and neither kills any worker: the hub brake stops only new claims, the local brake additionally blocks every
other spawn site (restart-resume, answer-resume, requeue respawn) but never a worker already running.
