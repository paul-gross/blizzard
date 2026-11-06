# Cadences

**While the `cadences` flag is off, cadences mint nothing.** A cadence is stored, edited, and read with the flag off,
but no chunk is minted from one until it is on ([flags.md](../flags.md)). While it is off, `blizzard hub cadence list`,
`get`, and `errands` print a line on stderr saying so.

A **cadence** is a project's rhythm: when, and how often, the routines it keeps should run. A **chore** is one routine a
cadence keeps, with the scopes it runs over, its run mode, and whether the chunks it mints are promoted. An **errand**
is one chore over one scope; it is derived from the chore, never configured. blizzard-context's
[`domain/cadences.md`](https://github.com/paul-gross/blizzard-context/blob/master/domain/cadences.md) owns what each of
these is and how they relate.

A cadence belongs to a project, like a [routine or a scope](./routines-and-scopes.md), and its slug is unique within it.
Every verb takes `--project <id|slug>` when the tenant holds several projects.

## The record

```yaml
slug: evening
name: Evening sweep
schedule:
  cron: "0 20 * * *"
  zone: America/Chicago
spacing: 10d
anchor: started
chores:
  - key: arch
    routine: architecture-drift
    scopes: all
    mode: delta
  - key: docs
    routine: docs-drift
    scopes: [blizzard, blizzard-context]
    promote: false
```

| Field      | Meaning                                                                                                                   |
| ---------- | ------------------------------------------------------------------------------------------------------------------------- |
| `slug`     | The cadence's handle within its project; immutable.                                                                       |
| `name`     | A display name; the slug when left out.                                                                                   |
| `schedule` | A five-field `cron` expression, read as wall-clock time in the IANA `zone`. Both are required.                            |
| `spacing`  | The least time between an errand's runs: a whole number and `m`, `h`, or `d`.                                             |
| `anchor`   | What the spacing counts from: `scheduled`, `started`, or `finished`; `started` when left out. Stated only with a spacing. |
| `chores`   | The whole list of chores, each keyed within the cadence by `key`.                                                         |

A cadence states a schedule, a spacing, or both; one with neither is refused. A six-field cron expression, a cron
expression that never fires, and an unknown zone are each refused `422`, naming the field.

A chore names its `routine` by slug. Its `scopes` is `all`, for every scope linked to the routine, a list of scope
slugs, or left out, for the routine's default scope. Its `mode` is `full` or `delta`, `full` when left out. Its `note`
is carried into each run's charge. Its `promote` is `true` when left out; `false` leaves each chunk it mints not ready.

A chore keeps its `chr_` id while its key stays in the list, across every edit. Removing a key and adding it back mints
a new chore.

## Attention

A cadence's view carries `attention`: each thing that would stop one of its errands, named by its `reason`, the chore's
key, and the scope where it stops one errand. A cadence that needs attention is still stored and edited; nothing about
it is refused for naming a routine or a scope that does not resolve. The reasons are `routine_unknown`,
`routine_retired`, `graph_unresolved`, `scope_unknown`, `scope_retired`, `scope_unlinked`, `no_scopes`, and `no_chores`.
A retired cadence carries none.

## Errands

An errand is one chore over one scope, and `GET /api/cadences/{cadence_id}/errands` lists them, chore by chore in
cadence order and by scope slug within a chore. Each shows its **state**, its **last run**, and its `due_at`:

| State     | Meaning                                                                      |
| --------- | ---------------------------------------------------------------------------- |
| `due`     | The next sweep pass will mint it.                                            |
| `running` | Its last run has not finished; a run waiting on a person counts as running.  |
| `waiting` | It is neither, and comes due at `due_at`.                                    |
| `stopped` | Its cadence is retired or attention stops it, even while a last run is live. |

A cadence's view counts its errands by state in `errand_counts`.

An errand comes due by its cadence's rhythm, counted from its last run's **reference**: the instant that run was
scheduled, or, for an errand that has never run, the instant it joined its chore. A schedule alone makes it due at the
first moment after the reference. A spacing alone makes it due once the spacing has run out from the anchor — the run's
start, finish, or scheduling — and a never-run errand is due at once. With both, it is due at the first moment after the
reference that is also at or after the end of the spacing. An errand whose last run is still live is not due.

A moment missed while the hub was down, or while a run was live, is not lost: the errand fires late, once, however many
moments passed. `due_at` is empty while an errand is running or stopped. For a `due` errand it may lie in the past, the
instant it was owed. A `due` errand mints only while the `cadences` flag is on.

A run's list and detail views carry `cadence`: the id, slug, and name of the cadence that minted it, and the id and key
of its chore. A run started by hand has none, and a run whose chore has since been removed names the chore's id with no
key.

## The CLI

```bash
blizzard hub cadence create evening --cron "0 20 * * *" --zone America/Chicago --spacing 10d [--anchor started] [--name TEXT]
blizzard hub cadence list [--include-retired]
blizzard hub cadence get evening
blizzard hub cadence errands evening
blizzard hub cadence edit evening [--name TEXT] [--cron EXPR] [--zone ZONE] [--spacing 3d] [--anchor finished]
                                  [--no-schedule] [--no-spacing]
blizzard hub cadence retire evening [--by WHO]
blizzard hub cadence enable evening [--by WHO]

blizzard hub cadence chore add evening arch --routine architecture-drift [--all-scopes | --scope S ...] [--mode delta] [--note TEXT] [--hold]
blizzard hub cadence chore edit evening arch [--routine R] [--all-scopes | --scope S ... | --default-scope] [--mode M] [--note TEXT] [--hold | --promote]
blizzard hub cadence chore remove evening arch
```

A cadence is named by its slug. `get` prints the count of its errands in each state, and `errands` lists them with each
one's state, last run, and `due_at`. `edit` changes only the options given; `--cron` or `--zone` alone keeps the stored
other half, `--no-schedule` clears the schedule, and `--no-spacing` clears the spacing and its anchor together. `retire`
is a reversible brake: a retired cadence mints nothing, `list` leaves it out unless `--include-retired`, and `enable`
restores it.

A `chore` verb is an edit of its cadence: it reads the cadence, rewrites its chore list, and writes it back on the
revision it read, so a cadence changed in between is refused `409` rather than overwritten. Each edit moves the
cadence's revision and writes a row to the [change log](./config-changes.md) naming each chore added, removed, or
changed by its key. `--hold` writes `promote: false`, and `--promote` writes it back.

Every write needs `graph:edit`. A write that leaves a live cadence with a promoting chore also needs `chunk:control`;
retiring needs only `graph:edit`.

Many cadences are kept more easily in a [configuration document](./config-documents.md), in its `cadences` section.
