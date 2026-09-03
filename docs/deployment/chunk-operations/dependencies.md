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
prerequisite that has not reached `done`. It names that one prerequisite and stops there: where the chunk it waits on is
itself blocked, the chain is not walked, and an operator who wants the root follows the naming one hop at a time. The
marking changes nothing about how a chunk is queued, claimed, grouped, deleted, or edited — it keeps the status it
derives, the rank it holds, and the list it lives in. Satisfaction is not stored: an edge is met when its prerequisite
reads `done`, read fresh at the point something consults it rather than cached on the edge itself, so declaring onto an
already-`done` prerequisite is an ordinary accepted edge that names no marking. A prerequisite absent from the fleet's
statuses — a standing edge onto a since-deleted id — still blocks, the conservative read.

## What declaring an edge does not yet do

A standing, unsatisfied dependency still changes nothing about how its dependent is claimed or shown on the board: it
denies no claim, and reaches no board surface.
