# Urgencies

Every chunk carries an urgency: how soon the tenant wants it worked. A tenant's urgencies are one ordered set, most
urgent first, and one live urgency of it is the tenant's **default**, the one every new chunk takes when nothing names
another. The set, its order, and the default belong to the tenant, not to a project.

## The set, the order, and the default

A new tenant starts with one urgency, `standard`, slate and with no mark, and its settings name `standard` as the
default ([tenants.md](./tenants.md#creating-renaming-and-deleting-a-tenant)). `blizzard hub migrate` gives every tenant
that existed before urgencies the same `standard` and default, and gives every chunk it already held that urgency.

The order runs from the most urgent to the least. A created urgency lands at the least urgent end unless it is placed
first or just after another. Two reorders move it later:

- **Set the whole order.** The list names every live urgency exactly once. A list that leaves a live urgency out, names
  a retired one, or names one twice is refused `422 urgency_order_incomplete`, and one naming an urgency the tenant does
  not hold `422 urgency_unknown`. Setting the order that already stands writes nothing.
- **Move one urgency** just after another, or first. A retired urgency, an urgency the tenant does not hold, and a move
  after itself are refused `422`. A move to where it already stands writes nothing.

The default is a field of the tenant's settings, a record every tenant holds from its creation and which is only shown
and edited. Changing the default moves no chunk; it decides only what is minted from then on. A default naming no
urgency of the tenant is refused `422 urgency_unknown`, a retired one `422 urgency_retired`, and an explicit `null`
`422`.

## The chain

A new chunk takes the urgency of the first link of its chain that names one:

1. The urgency the mint states: `--urgency` on `chunk ingest`, `item create`, `routine run`, and
   `garden-proposal accept`, or `urgency` on their requests, by id or slug.
2. For a chunk a cadence mints, its chore's override ([cadences.md](./cadences.md#the-record)).
3. For a routine's run, its routine's default urgency ([routines-and-scopes.md](./routines-and-scopes.md#routines)).
4. The tenant's default.

A stated urgency naming none is refused `422 urgency_unknown`, and a retired one `422 urgency_retired`. A chore's or a
routine's urgency that is retired names none, so the chain falls through to the next link. The chain is resolved under
the mint's locks, so an urgency retired while the mint is in flight never lands on its chunk. A `garden-proposal accept`
that mints no work item is refused `422` for stating an urgency.

A chunk's urgency is changed afterwards through `chunk set --urgency`
([chunk-operations/editing.md](./chunk-operations/editing.md#urgency)). Grouping keeps the surviving chunk's urgency.
Every chunk view carries `urgency`, the chunk's urgency as `{urgency_id, slug}`; `chunk list`, `queue get`, and the
header line of `chunk get` show its slug.

## On the board

**Admin → Urgencies** at `/admin/urgencies` lists the tenant's urgencies in order, with their marks, open-chunk counts
and default; a filter includes retired records. With `config:edit`, create and edit a name, color and emoji or image
mark, drag live rows to reorder them, and choose the default. The slug is fixed after creation. The detail offers retire
or enable; retiring an urgency with open work asks where to move it and also requires `chunk:control`, and the default
must first be changed. Detail also shows its change history. On a phone the administration screen is read-only and shows
CLI commands for these actions.

The [board](./board.md#urgency-on-the-board) shows a pill on unfinished, non-default work and lets a person with
`chunk:control` change a chunk's urgency. New hub items and accepted garden proposals offer a choice starting at the
tenant default. A routine run can choose an urgency or follow the routine default; a routine can follow the tenant
default, and a cadence chore can follow the routine default. These choices use the [chain](#the-chain).

The board polls its urgency configuration every 15 seconds while active, and refreshes on focus and reconnect, so a
change made elsewhere normally appears within that interval. Its lists remain in rank order; the runner's
[next-claim explanation](#a-runners-next-claim) is a snapshot of the hub's current knowledge, not a promise of the next
claim.

## The fields

| Field   | Meaning                                                                                                                                                                                                                              |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `slug`  | The handle, 1 to 64 lowercase letters, digits, and single inner hyphens. Fixed at create: an edit stating one is refused. A slug any urgency holds, retired or not, is refused `urgency_slug_taken`.                                 |
| `name`  | The display name, 1 to 128 characters with no leading or trailing whitespace; the slug when a create leaves it out.                                                                                                                  |
| `color` | A palette color (`red`, `orange`, `amber`, `lime`, `green`, `teal`, `cyan`, `blue`, `indigo`, `violet`, `pink`, `slate`) or a `#rrggbb` hex color; a create that leaves it out takes the palette color the live urgencies use least. |
| `emoji` | An optional mark: at most 16 code points, with no whitespace, control character, or ASCII letter, and a digit, `#` or `*` only as a keycap's base.                                                                                   |
| `icon`  | An optional mark: a PNG or WebP image of at most 32 KiB, its bytes matching its stated media type. SVG is refused.                                                                                                                   |

An urgency carries at most one mark: a write leaving both an emoji and an icon set is refused
`422 urgency_mark_conflict`. An invalid field is refused `422`, naming the field. `GET /api/urgencies/{key}/icon` serves
the icon with its media type, the digest of its bytes as the `ETag`, `Content-Security-Policy: default-src 'none'` and
`X-Content-Type-Options:
nosniff`, so a browser draws it and never runs it; it answers `304` when `If-None-Match` names
the digest, and `404` for an urgency carrying no icon.

A view of an urgency, from `GET /api/urgencies` or `GET /api/urgencies/{key}`, adds its `place` in the live order (`1`
the most urgent, `null` once retired), whether it is the `default`, its `revision`, and `open_chunks`, how many chunks
not yet done or stopped carry it. `{key}` is an urgency's id or its slug.

```yaml
urgencies:
  - slug: urgent
    name: Urgent
    color: red
    emoji: "🔥"
  - slug: standard
    name: standard
    color: slate
urgency_order: [urgent, standard]
settings:
  default_urgency: standard
```

A [configuration document](./config-documents.md) declares the set in these three sections.

## Retiring with work moved, and enabling

Retiring an urgency takes it out of the live order. Its open chunks move to the urgency `move_to` names, in the same
write; finished chunks keep the urgency they carried. In the same write, every routine's default urgency and every
chore's override naming it is cleared, each with its own change row, so their runs fall through to the next link of the
chain. A retire is refused, in this order:

1. `409` of problem type `urn:blizzard:problem:urgency-is-default` for the tenant's default. Change the default first.
2. `409` of problem type `urn:blizzard:problem:urgency-in-use` when open chunks carry it and no `move_to` is named; the
   refusal counts them in its `open_chunks` member.
3. `422 urgency_unknown`, `urgency_retired`, or `urgency_move_to_self` for a `move_to` naming no urgency, a retired one,
   or the urgency being retired.

A retire that moves open chunks needs `chunk:control` beside `config:edit`, and is refused `403` without it. Retiring a
retired urgency writes nothing. The retire's change row lists `move_to` and `moved_chunks` beside the flip of `retired`
([config-changes.md](./config-changes.md)).

Enabling brings a retired urgency back at its standing place in the order. It moves no chunk back and restores no
cleared routine default or chore override. Enabling a live urgency writes nothing.

Every edit, retire, and enable takes `If-Match` on the urgency's `revision`, and the settings' edit on theirs; a stale
one is refused `409`.

## Claim order

A tenant's ready chunks have two orders. **Rank order** is the order an operator sets with the queue's reorder and
reposition writes, and what `GET /api/queue`, `blizzard hub queue get`, and the board's lists show. **Claim order** is
the order runners are offered work in: the ready chunks grouped by their urgency's place in the set, most urgent group
first, and within a group in rank order. It is worked out on every read from the two, never stored, and changes the
moment either changes: move an urgency in the set, or set a chunk's urgency, and the next peek walks the new order. With
one live urgency, claim order is rank order.

Both runner peeks, `GET` and `POST /api/fleet/queue/peek`, walk claim order, so an urgent chunk ranked last is offered
before a standard chunk ranked first. The positions they return are indexes into the whole tenant's claim order. Runners
of every release get this with no upgrade, because the peek's wire is unchanged.

Hold and pass-over apply to claim order. Under pass-over, the walk steps past a chunk the runner cannot use, one that is
blocked or that the runner cannot work, to the next in claim order, down a group when an urgency is spent. Under hold,
the walk stops at the first chunk it cannot use, or at each project's first when the peek names projects, so an urgent
chunk the runner cannot use holds its project's less urgent work behind it.

An urgent chunk behind an unfinished prerequisite is not raised, and neither is the prerequisite. Say chunk C is
`urgent` and depends on chunk D, which is `standard` and not done. Under pass-over the runner is offered the first chunk
after C that it can use, and D keeps its place in the standard group. Under hold the runner is offered nothing for C's
project until D finishes. To get C worked sooner, give D the urgency too.

### A runner's next claim

`GET /api/runners/{runner_id}/next-claim` explains, for one runner, what its walk reaches for next. It walks claim order
from what the hub holds of the runner: the projects it serves, its bindings, and its latest capability snapshot. It is
the hub's best account, not a promise: the runner peeks with what it holds at that moment, and the queue can change
before it does. A retired or paused runner is answered like any other.

On the Fleet tab, runner rows in host detail and hostless runner detail show **Next**: a link to that chunk with its
project and urgency, or `Nothing claimable`. Expanding **Passed** lists the chunks the walk passed, with links and
reasons. A blocked entry links its first prerequisite and shows its status when available and how many more there are;
an ineligible entry names the possible missing harness, model, or graph. The section counts passed entries omitted from
the bounded list, reports work ahead from unserved projects, and shows what a per-project hold would reach and where it
would stop. Loading and read failures are shown separately from `Nothing claimable`.

The board refreshes this explanation on chunk, queue and runner changes, with a 45-second polling backstop; urgency
configuration is polled every 15 seconds as described [above](#on-the-board). Each field of the API answers one
question:

| Field          | Answers                                                                                                                      |
| -------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `next`         | Which chunk the runner reaches for next under pass-over; absent when it can use none.                                        |
| `passed`       | Which chunks of its projects it steps past before `next`, at most twenty, each with its `reason`.                            |
| `reason`       | Why a chunk was passed: `blocked`, naming its first unfinished `prerequisite` and the `prerequisite_count`, or `ineligible`. |
| `passed_total` | How many chunks it passes in all, listed or not.                                                                             |
| `unserved`     | How many chunks ahead of `next` belong to projects the runner does not serve.                                                |
| `if_holding`   | Which chunk it would reach for under a per-project hold.                                                                     |
| `held_at`      | Where each served project's hold stops, at a chunk it cannot use, with its project and `reason`.                             |

Each entry names its chunk, graph, project, urgency, and `position` in the whole tenant's claim order. In the worked
example, C appears in `passed` as `blocked` with D as its prerequisite, and `next` is the first chunk after C that the
runner can use.

## The CLI

```text
blizzard hub urgency create SLUG [--name TEXT] [--color COLOR] [--emoji TEXT | --icon FILE] [--after SLUG | --first]
blizzard hub urgency list [--include-retired]
blizzard hub urgency get SLUG
blizzard hub urgency edit SLUG [--name TEXT] [--color COLOR] [--emoji TEXT | --icon FILE | --clear-mark]
blizzard hub urgency retire SLUG [--move-to SLUG]
blizzard hub urgency enable SLUG
blizzard hub urgency order SLUG...
blizzard hub urgency position SLUG (--after SLUG | --first)

blizzard hub settings get
blizzard hub settings edit --default-urgency SLUG

blizzard hub runner next RUNNER
```

`list` prints the urgencies in order, each with its place, name, color, mark, and open-chunk count, the default marked.
`--icon` reads a `.png` or `.webp` file. On `edit`, `--emoji` replaces any icon and `--icon` any emoji. `retire` without
`--move-to`, on an urgency open chunks carry, prints how many and the command to run with `--move-to`:

```console
$ blizzard hub urgency retire urgent
Error: urgency urgent carries 2 open chunks; name where they go:
  blizzard hub urgency retire urgent --move-to <SLUG>
```

`runner next` prints the runner's next chunk, then each passed chunk with its position, urgency, and reason, a blocked
one naming its prerequisite and how many more it has. When the hub passed more chunks than it lists, a last line gives
how many more. With no usable chunk, the `next` line says so:

```console
$ blizzard hub runner next rn_01JC4Z8Q3XKQ1V5W2M7N9P0R6T
next  3  ch_next  urgency=standard
  passed  0  ch_dep  urgency=urgent  blocked by ch_pre (+2 more)
  passed  1  ch_gpu  urgency=-  ineligible
```

Each verb takes `--tenant` and `--json`. Reading takes `fleet:view`; every write takes `config:edit`.

| Verb               | Route                                |
| ------------------ | ------------------------------------ |
| `urgency create`   | `POST /api/urgencies`                |
| `urgency list`     | `GET /api/urgencies[?retired=true]`  |
| `urgency get`      | `GET /api/urgencies/{key}`           |
| `urgency edit`     | `PATCH /api/urgencies/{key}`         |
| `urgency retire`   | `POST /api/urgencies/{key}/retire`   |
| `urgency enable`   | `POST /api/urgencies/{key}/enable`   |
| `urgency order`    | `PUT /api/urgencies/order`           |
| `urgency position` | `POST /api/urgencies/order/position` |
| `settings get`     | `GET /api/settings`                  |
| `settings edit`    | `PATCH /api/settings`                |
| `runner next`      | `GET /api/runners/{id}/next-claim`   |
