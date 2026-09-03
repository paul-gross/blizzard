# Declaring and releasing a chunk dependency

A chunk can name another chunk it depends on: the dependent, and the prerequisite it names.
`POST /api/chunks/{chunk_id}/dependencies` declares the edge and `POST /api/chunks/{chunk_id}/dependencies/release`
releases a standing one; both are `CHUNK_CONTROL`-gated, take a JSON body of
`{"prerequisite_chunk_id": "...", "by": "..."}` with `by` optional (default `"operator"`), and answer `202 Accepted`
with a `ChunkDependencyEdgeView` — the written or already-standing edge's `dependency_id`, both chunk ids,
`declared_at`/`declared_by`, and nullable `released_at`/`released_by` — the only way to learn an edge's minted id, since
neither route addresses one by it. A declare mints a fresh row only when no edge for the pair currently stands — an
already-standing pair is an idempotent no-op, below — so the same pair may accumulate several rows over time, one per
declare-after-a-release, none ever revived. Releasing a row is recorded on it, not a deletion — `released_at`/
`released_by` are set together, once, and the row is never removed.

## Declaring an edge

Declaring is admitted only while the dependent reads `not_ready` or `ready` with no runner holding it — the same
`not_ready`/`ready` vocabulary [editing.md](./editing.md) uses for its own unclaimed window, though not the same window:
this one is a bare `PRE_CLAIM_STATUSES` membership test, carrying neither the never-moved nor the retired-graph
condition editing's window additionally binds. Any other status refuses 409. Declaring a pair that already stands is an
idempotent no-op reporting the standing edge back, checked before the prerequisite is looked at, at all, so it never
refuses even once the dependent has left the status window or the prerequisite has since been grouped away or deleted. A
declaration that would close a cycle in the standing dependency graph is refused 409; a self-edge (a chunk named as its
own prerequisite) is the trivial cycle and is refused the same way, not a special case.

A dependent the hub cannot resolve — never minted, itself grouped away or deleted, or one a race deletes between
resolving it and this write landing — is 404. The prerequisite alone gets the ephemeral/never-minted split: a
prerequisite id the hub never minted is 404, while one that is ephemeral — grouped away or deleted — is refused 409
instead, since it is a real id the hub once held rather than one that never existed. Nothing else narrows what may be
named as a prerequisite: any chunk the hub holds is legal, on any graph, from any work source.

## Releasing an edge

Release names the same ordered pair rather than a minted edge id. It has no status window: it is admitted whenever the
edge stands, whatever status the dependent reads and whatever became of the prerequisite — including one since deleted.
A release naming a pair with no standing edge is refused 409; a dependent the hub cannot resolve — never minted, or
itself grouped away or deleted — is 404.

## The blocked marking

A standing, unsatisfied dependency derives a **blocked marking** — a nullable field carried beside `status` on
`GET /api/chunks`, `GET /api/chunks/{chunk_id}`, `GET /api/queue`, and `GET /api/backlog`, naming the earliest-declared
prerequisite that has not reached `done`. What the marking means, its one-hop scope, and the `not_ready`/`ready` window
it is confined to are `blizzard-context:/domain/work/statuses.md`'s to say. Satisfaction is not stored: an edge is met
when its prerequisite reads `done`, read fresh at the point something consults it rather than cached on the edge
itself, so declaring onto an already-`done` prerequisite is an ordinary accepted edge that names no marking. A
prerequisite absent from the fleet's statuses still blocks, the conservative read — but deletion now refuses a
standing edge onto a live prerequisite (issue #460), so this conservative read only guards the accepted residual race
between a status read and a concurrent write, not an ordinary reachable path.

A chunk currently named as another's prerequisite cannot itself be deleted while that edge stands: deletion is refused
409, naming the dependents. Deleting the *dependent* chunk instead is unaffected — it succeeds, and releases that
chunk's own outgoing standing edges as part of the same delete, rather than refusing.

## What a standing edge does to claiming

A standing, unsatisfied dependency denies a claim on its dependent outright: `POST /api/fleet/routes` answers `409`
with the marking's own body shape — `chunk_id` and `prerequisite_chunk_id`, distinct from the conflict and terminal
`409`s a claim can otherwise answer with — re-derived fresh under the claim lock rather than trusted from an earlier
read, so a peek-then-claim race can never slip a blocked chunk through.

A runner's own FILL step does not have to run into that denial to make progress: `GET /api/fleet/queue/peek` already
carries the marking on every entry it returns, and a runner reaches past a marked head for the first unmarked entry
in the peeked list by default, rather than spending a claim attempt it already knows will be refused. An operator who
sets `[queue] strict = true` in that runner's config opts out of reaching ahead — a marked head yields no entry and
FILL idles for the tick rather than trying a later one. Either way the claim-time denial above still stands as the
structural guarantee: reach-ahead is an efficiency over the peek, never a replacement for it.

What a standing edge does not yet do is reach the board: no board surface shows it.
