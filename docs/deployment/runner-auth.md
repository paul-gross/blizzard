# Runner authentication

Runner authentication is machine identity, a runner authenticating itself to the hub — distinct from human login
([human-auth.md](./human-auth.md)).

Every outbound runner-to-hub call — the reconciliation loop and the work-items proxy alike — attaches
`Authorization: Bearer` with the token the hub issued when it added the runner, and the hub takes the runner's identity
from that token alone. A fleet call with no token, or with one the hub never issued, is refused with a `401` under every
configuration: no runner joins without being added.

A worker's lease token, which it presents to its runner's own API under `/runners/{runner_id}/` on the host, also
authorizes its `POST /runners/{runner_id}/v1/traces` span export, and under `harness_telemetry` its `/v1/metrics` and
`/v1/logs` exports under the same prefix;
[tracing.md](./tracing.md#worker-spans) owns what the runner keeps from them.

A runner reading back its own shipped transcript segments (`GET /api/fleet/chunks/{chunk_id}/transcript-segments`) is
further held by that route's own ownership check, which refuses another runner's token. The chunk-scoped garden
findings/proposals and the six analytics counts/spend reads (`GET /api/fleet/chunks/{chunk_id}/garden/...`,
`.../analytics/...`) are not: each is confined only to the chunk carrying a routine run's context, never to the calling
runner's own identity, so any runner the hub added may read them.

`route_token_mode`, scaffolded into `blizzard-hub.toml` by `hub init` and defaulting to `warn`, requires the
per-acquisition route capability token on every chunk-scoped write. Under `warn` a missing, invalid, or mismatched route
token is logged and the write proceeds; under `enforce` the write is rejected as a semantic failure. Flip it to
`enforce` only after outbound buffers carrying pre-upgrade token-less facts have drained — `warn` already covers that
window, so there is no separate grace period. A `blizzard-hub.toml` that still sets the retired `runner_auth_mode` boots
with the key ignored.

## Adding a runner

`blizzard hub runner add <name>` mints the runner's id — `rn_` followed by a ULID, which never changes — and its bearer
token together, and records the runner under that name as never connected. It prints both once, the token as the
`BZ_HUB_TOKEN_<NAME>=<token>` line a host directory's `.env` takes — `<NAME>` is the runner's name upper-cased with
every character outside `A-Z0-9` replaced by `_`, so `r-claude` prints `BZ_HUB_TOKEN_R_CLAUDE=…`; there is no read-back.
The runner's first registration with that token resolves it to the id, and from then on the `name` its `[[runner]]` in
`blizzard-host.toml` declares replaces the one `add` was given
([remote-runner.md](../remote-runner.md#point-it-at-the-hub) owns naming).

A fresh `blizzard-runner init` scaffolds the runner as `runner-local` and looks for `BZ_HUB_TOKEN_RUNNER_LOCAL`, so to
keep a runner added by hand, add it as `runner-local` and put the line it prints in the new directory's `.env` before
running init; a token under another name's variable is not found, and init adds a second runner. To give the runner
another name afterwards, change `name` and rename the variable to match
([remote-runner.md](../remote-runner.md#point-it-at-the-hub)).

`add` adds the runner in the tenant the call acts in — `--tenant <tenant_id>`, needed only when you belong to several
([tenants.md](./tenants.md#naming-the-tenant-a-call-acts-in)). The runner belongs to that tenant for as long as it
exists: its token names the tenant on every fleet call, and a runner never moves between tenants.

`add` takes a repeatable `--project <id|slug>`, the projects the runner serves; a project the tenant does not hold
refuses the add. Without it, the runner serves the tenant's project when the tenant holds one, and nothing when it holds
several. `blizzard-runner init` writes the runner's `projects` into a fresh host file as a binding of `default`, or of
each project its own repeated `--project` names, and adds the runner serving them
([projects.md](./projects.md#runners-serve-projects)).

`blizzard-runner init <dir>` registers the host at the hub its config names — `--hub <url>` or else `BZ_HUB_URL` sets
it on a fresh config — then joins each `[[runner]]` under it, writing each token the hub issues as it is issued.

It looks for the host's token first, as the host resolves it: `BZ_HOST_TOKEN` in the process environment, the host
file's `host_token`, then `BZ_HOST_TOKEN` in the directory's `.env`. A held host token is checked with
`GET /api/fleet/host`, and the host it names is kept. Holding none, init adds a host named after the first
`[[runner]]` (`POST /api/hosts`) and writes its token to `.env` as `BZ_HOST_TOKEN`; a name a live host of the tenant
holds stops init, naming the conflict. On a hub with `auth.mode = "none"` that needs no sign-in; on one that requires
it, the add runs under your `blizzard hub login` session for that hub, and with no session init stops and says so. It
adds the host in the tenant `--tenant <tenant_id>` names, by id, never by name; without the flag, in the only tenant you
may enter, and when you may enter several it stops, listing them, ids beside names. A new host adopts each runner whose
held token the hub knows. A held host token whose host serves another tenant than `--tenant` names stops: a host never
moves between tenants. A hub that answers the host routes `404` predates hosts, and init stops.

Every runner init adds is added under the host's own credential (`POST /api/fleet/host/runners`), in the host's tenant,
with no sign-in. With a runner token, init first asks the hub whose it is (`GET /api/fleet/identity`, under the
runner's own token and with no side effects) and acts on the answer:

| The hub answers                                             | `init`                                                                                                                                         |
| ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| the token's runner                                          | keeps the token and adds nothing, when the hub holds the runner under this host; otherwise stops, naming `hub runner-host adopt`                |
| the token's runner, in another tenant than `--tenant` names | stops and adds nothing, naming both tenants: drop `--tenant` to keep that runner, or set up a new runner in that tenant from a fresh directory |
| an unknown token                                            | stops, adds nothing, and leaves `.env` untouched, naming the hub it asked                                                                      |
| a revoked token                                             | stops, naming `hub runner enroll <id>` — rotation keeps the runner's id, and an add would not                                                  |
| a retired runner                                            | stops, naming `hub runner reinstate <id>` then `enroll <id>` — retiring revoked the token, and an add would undo the retirement                |
| nothing, or an error                                        | fails and adds nothing, so an unreachable hub never gains a duplicate runner                                                                   |

Both stops on a revoked token or a retired runner say where the new token goes: the runtime dir's `.env`, or the
variable that supplied the token init presented, which overrides that file.

An unknown token means one of two things, and only you can tell which. The hub's data was deliberately reset — a
development hub started over — and the runner's id went with it: re-run init with `--allow-readd`, and it adds a fresh
runner and replaces the token. Or the config names the wrong hub, one that never added this runner: re-adding there
would mint a runner at that hub and overwrite the only token the runner holds for its own, so fix `hub_url` instead.
Init never re-adds without the flag.

A host token the hub does not know reads the same way. With `--allow-readd`, init sets the host's store aside, adds the
host afresh, and adds every `[[runner]]` again under it. A host token the process environment or the host file supplies
is never replaced: an unknown one stops init even with `--allow-readd`, since a replacement in `.env` would go unread. A
host token the hub refuses as a retired host's stops init too.

Before it re-adds, init sets the store aside under a timestamped name beside it and migrates a fresh one,
so the new runner never inherits the old one's leases and buffered facts. Nothing is deleted: the old store keeps them.
It refuses while the runner's daemon is running. The store is the host's, shared by every runner it configures, so a
runner is never re-added in place beside another: setting the store aside would drop that runner's backlog. Init
refuses, naming `blizzard runner retire` and `blizzard runner add`, which replace the one runner and leave the store
as it is; a host re-added sets the store aside once and adds every runner again over it. A runner holding no token whose store still belongs to a runner stops
the same way an unknown token does: put that runner's token back, or re-run init with `--allow-readd`.

A runner's token variable is derived from its `name`, as above; the host file has no key to rename it. Every runner verb
resolves a runner's token from the first of the process environment's `BZ_HUB_TOKEN_<NAME>`, the host file's
`[hub_tokens]` table keyed by the runner's name (`[hub_tokens] r-claude = "…"`), and `BZ_HUB_TOKEN_<NAME>` in the host
directory's `.env`, which init writes. A host file that holds `[hub_tokens]` or `host_token` and is readable by anyone
but its owner draws a start-up warning. Init replaces only the token's line in `.env`, keeps every other line in systemd
`EnvironmentFile` syntax, writes the file owner-only (`0600`) through an atomic rename, and so replaces a symlinked
`.env` with a regular file. When init keeps a token that a hand-written `.env` holds, it makes that file owner-only too,
leaving its bytes as they are, or warns when it cannot. The daemon reads only the token from that file. A token the
process environment or the host file supplies wins, so when the hub does not know it, init stops rather than write a
replacement that would go unread.

If the hub adds the host but its token cannot be written, init exits naming
`blizzard hub runner-host retire <id> --hub-url <hub>`. If the hub adds a runner but its token cannot be written, init
exits naming
`blizzard hub runner retire <id> --hub-url <hub>`: the hub holds that runner as never connected, under a token nobody
has. Retire it, then re-run init. An init interrupted between the two leaves the same runner, which `hub runner list`
shows as never connected.

## Rotation

`blizzard hub runner enroll <id>` rotates an added runner's token: it mints a new one, prints it once the same way, and
records the token it replaces as revoked, the one `add` minted included. It 404s on an id the hub never added.

## Revocation

A revoked token is refused with a `401` on every fleet route. The hub records each hash it revokes and never clears it,
so a token revoked once stays dead even after the runner is enrolled afresh.

`blizzard hub runner revoke-token <id>` revokes the current token and leaves the runner added; it refuses with a `409`
when the runner holds no token. The runner is refused until `enroll` mints a new one.

`blizzard hub runner retire` revokes the runner's token as part of retiring it
([control-verbs.md](./control-verbs.md#runner-level-brakes)). `enroll` on a retired runner refuses with a `409` naming
`reinstate`; after `reinstate`, `enroll` mints a fresh token.

`add` and `enroll` require the `runner:add` permission, and `revoke-token`, `retire`, and `reinstate` the
`runner:retire` permission; a tenant's `admin` holds both, in that tenant.

## Hosts

A host is the machine a set of runners lives on, recorded in the tenant that added it. It has its own id — `hs_`
followed by a ULID — a name, and its own bearer token, separate from every runner's.
`blizzard hub runner-host add <name>` mints the id and token together and prints both once, the token as a
`BZ_HOST_TOKEN=<token>` line; there is no read-back. A host reads its token as `BZ_HOST_TOKEN`, from the process
environment, the host file's `host_token` key, or the host directory's `.env`, so the printed line goes into the host
directory's `.env` as it is. The group is `runner-host` because `blizzard hub host` launches the host daemon. Host names are unique among a
tenant's live hosts, and a taken name refuses the add.

The host token reaches only the host's own routes, under `/api/fleet/host`: reporting the machine's workspace, reading
its record back, adding a runner under the host, retiring one of its runners, and pushing the host's own facts (its
subscription usage samples and misses) to `/api/fleet/host/events`, de-duplicated against a mark of the host's own. It
is refused on every runner route, and a runner's token is refused on the host routes. A host acts only on runners it
holds; another tenant's runner is refused as unknown.

`add --adopt <runner_id>` (repeatable) adds the host and takes the named runners under it in one transaction, so there
is no moment the host exists without them. `blizzard hub runner-host adopt <host_id> <runner_id>` takes an existing
runner under a host later; `release` takes it out again. A runner is held by at most one host, and
`blizzard hub runner
add --host <host_id>` adds a new runner already under one. `runner-host list` shows the tenant's
live hosts, `--all` adds the retired, and `show <host_id>` prints one with its runners.

A host's report is recorded as it arrives and never refused over its content; a name the report carries that another
live host holds is not recorded. The hub publishes a `host-changed` event only when a report changes what it holds.

A hosted runner's bindings and subscription are checked against its host's latest report. A binding whose workspace the
host has not reported, and a subscription outside the host's roster, are flagged on the runner's views and in the
registration answer, never refused; the hub offers the runner no work through a flagged binding, and none at all under a
flagged subscription.

`runner-host revoke-token <host_id>` revokes the host's token and leaves the host recorded; `enroll <host_id>` mints a
new one, printed once as a `BZ_HOST_TOKEN=<token>` line, and records the token it replaces as revoked. A revoked hash stays dead, as for a runner.
`runner-host retire
<host_id>` revokes the token and retires the host, then retires each of its runners; it refuses
while a runner still holds routes unless `--force` is given, and `--by` records who retired it. Between the host's
retirement and its last runner's, a runner is still held but its host is gone. `enroll` on a retired host refuses with a
`409`.

The writes require the `runner:retire` permission and the reads `fleet:view`.

The board shows each host as a card over the runners it holds. To an identity holding `runner:retire`, the card carries
retire, revoke-token and enroll. Retire and revoke-token each ask for confirmation first. A retire the hub refuses
shows the refusal and asks again before forcing it. Enroll shows the new token once. Releasing a runner back to standing
alone is a CLI and API verb (`blizzard hub runner-host release`, `POST /api/hosts/{host_id}/releases`), not a button.

On a desktop-width screen the Fleet tab lists the hosts, then the runners under no host, and shows the selected one
beside the list. A host's detail carries the same retire, revoke-token and enroll in its title bar, with the same
confirmations, over its connection facts, workspaces, subscription usage and runners.

### A registered host

A host that holds a host token is **registered**; one that holds none is unchanged, runs one runner, and that runner
reports itself as it always has. A registered host:

- **Reports itself on every host tick.** It posts its first `public_url`, the panel's sign-in callback URIs, each
  workspace with the environments it holds, and the `[[subscription]]` roster it declares to `POST /api/fleet/host`,
  and its runners stop sending a roster of their own. It reports once before any runner ticks, so a runner the hub has
  released from the host never claims across a restart while the hub answers; a report that fails or times out starts
  the runners anyway.
- **Records who it is from the hub's first answer.** The host id and tenant land once in the store's `host_identity`
  row and are never edited. A later answer naming another host or tenant is a refusal of the host's token, not an
  update: the runners keep working, the host's facts stay buffered, and the panel shows the refusal.
- **Drains its own fact lane.** Its subscription usage samples and misses ride the host's lane, `host_outbound_buffer`,
  to `POST /api/fleet/host/events`, never a runner's lane, and leave only while the latest report was answered with the
  host's own identity. Against a hub from before hosts, which answers the host routes `404`, the runners keep working
  and the facts wait in the buffer.
- **Runs several runners.** The host file may hold more than one `[[runner]]`, each with its own token; without a host
  token, a second `[[runner]]` is a host error.
- **Halts a runner its hub does not hold.** From each answered report, the host judges every runner: held under the
  host, not held, or standing in another tenant than the host's. A runner not held, or in another tenant, halts alone —
  it drives no lease and delivers nothing, its hub proxy answers `503`, and the others keep working — and resumes within
  a host tick of the hub holding it again. A hub that has not answered, refuses the host's token, or predates hosts
  never halts a runner this way.

### Adding and retiring a host's runners

`blizzard runner add <name> --project <p>` adds a runner to a registered host under the host's own token, with no hub
sign-in: the hub mints the runner under the host (`POST /api/fleet/host/runners`), then `add` writes its token to the
host directory's `.env` as `BZ_HUB_TOKEN_<NAME>`, creates `runners/<name>/`, and appends its `[[runner]]` with the id the
hub minted — in that order, so an entry never exists without its token. `--project` repeats; `--workspace` names the
host's workspace each project runs in — once for all, or once per `--project` — and defaults to the host's only one;
`--agents` likewise, defaulting to `1`. A `--workspace` the host file does not declare, or a name a `[[runner]]` already
holds, refuses the add before it reaches the hub. A running host starts the new runner at its next start, and `add`
says so.

`blizzard runner retire --runner <name|id>` retires the runner at the hub under the host's token, forgets its identity
in the host's store, then removes its `[[runner]]`, so the store names only runners the host still configures and the
next start or `init` serves the rest. A runner still holding chunks refuses the retire, naming them; `--force` releases
them. A running host halts the runner at its next tick, the hub refusing its token, and `retire` says so; the runner's
other rows stay in the host's store, inert. Both verbs refuse a host holding no host token, naming `blizzard hub runner-host add`.

`GET /api/host/registration` reads where the host stands — `unregistered`, `pending` until its first report,
`registered` with its host id and tenant, `refused`, `no_hosts`, or `unreachable` — with the facts waiting on its lane.
The host page of the panel shows it. A served runner its hub halts is listed by `GET /api/host/runners` as `halted`,
naming why, and its own views still read.

### Ingesting a runner from another store

`blizzard runner ingest <source> --runner <name|id> [--workspace-id <id>] [--dir <host dir>]` moves one runner into
this host: its rows, its configuration and its token. The source is another host's directory, an archive store that
`blizzard runner store reset --runner` wrote, or a runner directory that has not graduated to a host.
Take the runner under this host at the hub first: `blizzard hub runner-host release <host_id> <runner_id>` on the
host that holds it, then `blizzard hub runner-host adopt <host_id> <runner_id>` on this one.

The ingest reads the source only through a copy taken with SQLite's backup API over a read-only connection, migrated to
this build's schema, so the source's store is not written until the runner's rows leave it. Then it:

1. copies the runner's rows into this host's store in one transaction, which records the ingest;
2. appends the runner's `[[workspace]]` unless this host declares one with the same root and environments, its
   `[[subscription]]` unless this host declares that slug, and its `[[runner]]`, with each path key that points into
   the source rewritten to a copy of that file or directory under `runners/<name>/`;
3. writes its token to this host's `.env` as `BZ_HUB_TOKEN_<NAME>` and removes it from the source's, so the source can
   no longer run the runner;
4. deletes the runner's rows from the source. A source store left with no runner is moved aside whole under a
   timestamped name.

An archive carries rows only, so the runner's `[[runner]]` and token must already be in this host, as a reset leaves
them; ingest refuses an archive otherwise. The source host keeps the runner's `[[runner]]`, which halts there for want
of its token.

A runner directory that has not graduated is read as the host of one it would graduate to
([upgrade.md](../upgrade.md)): its store is `data/runner.db`, its `[[workspace]]` and `[[runner]]` come from
`blizzard-runner.toml` by graduation's key placement, and the runner takes the id its store records. Its token is read
from `BZ_HUB_TOKEN`, or the variable the file's `token_env` names, and that variable is what leaves the source's
`.env`. A runner before hosts takes no lock, so only its `runner.sock` is probed. Its store holds that one runner and
stays at the schema it ran, so the last step moves it aside whole rather than deleting rows from it. A file declaring
several `[[subscription]]`s, or a `db_url` naming another store, is refused: graduate that directory by hand, then
ingest the host it becomes.

The ingest refuses, changing nothing, when:

- this host or the source is running: its lock is held, or something answers on its `host.sock` or `runner.sock`;
- this host holds no host token, its hub does not hold the runner under it — the refusal names the `adopt` to run —
  or the runner's identity in the source names another tenant than this host's. A host that has not recorded its
  identity yet records the hub's answer;
- the runner holds an open elicitation or takeover in the source;
- this host already declares a runner of that name or id, or its store holds the runner from no ingest of this source;
- this host gives the runner's workspace id to another root or set of environments. `--workspace-id` names a new id,
  which the copied rows and the workspace's prompt override take. It is refused on a runner bound to more than one
  workspace, and for an id this host gives to another workspace. A source workspace that is one of this host's under
  another id is refused, naming that id for `--workspace-id`;
- a held environment of the runner is one a runner of this host holds;
- the source's `.env` holds no token for the runner, or this host's holds a different one;
- both stores hold a different prompt override for one workspace, naming both;
- the source store is at a revision this build does not know.

Re-running an interrupted ingest completes it: a runner this host's store holds from an ingest of the same source
skips the copy and finishes the configuration and the removal. If the source has run since the copy, its liveness stamp
moved, and the ingest refuses, naming both stores.

## A token that names another runner

At the head of every tick the runner asks the hub which runner its token belongs to (`GET /api/fleet/identity`) and
compares the answer with the runner its host file configures, the `[[runner]] id` the store is bound to. When they
differ — a wrong token, or a `hub_url` pointing at another hub — the runner refuses to drive its leases or deliver its
buffered facts, and checks again on the next tick, so putting things right resumes it with nothing lost. A token the hub
refuses — unknown, revoked, or its runner retired — is a refusal of its own, shown as `REFUSING:` with the hub's reason
and checked again each tick the same way. A hub that cannot be reached leaves the verdict as it stood: an outage never
reads as a mismatch.

The runner logs the refusal once, at warning severity, naming both runner ids and what restores it, and
`blizzard runner status` shows it:

```text
REFUSING: token belongs to runner rn_…, the configured runner is rn_… — set BZ_HUB_TOKEN_<NAME> to the token of runner rn_…, or correct `[[runner]] id` in <dir>/blizzard-host.toml
```

While refusing, each check first re-reads the token from the runtime dir's `.env`, so correcting the token there resumes
the runner in-process, with no restart. A token from the process environment — `BZ_HUB_TOKEN_<NAME>` — or from the host
file's `[hub_tokens]` overrides `.env` and stays fixed until the runner restarts; the runner logs that once when it
starts refusing.

When the store, not the token or the `id`, is what is wrong, `blizzard runner store reset <dir>` moves the store aside
under a timestamped name and migrates a fresh one in its place. It deletes nothing: the old store keeps its undelivered
facts and in-flight leases beside the new one. It refuses while the host holds the directory or a pre-host daemon
answers on its `runner.sock`, and for a store that is not a SQLite file. Start the host again, and the fresh store is
bound to the configured `[[runner]] id`. A store that names the runner is not reset: a runner with history keeps its
facts at the hub, and the fresh store drops every fact below the hub's high-water mark, so restore the right store
instead. Reset only a store that is foreign to the configured runner and holds nothing to keep.

`blizzard runner store reset <dir> --runner <name|id>` resets one runner alone. It writes that runner's rows to an
archive store beside `host.db`, named `host.db.<runner>.<UTC stamp>`, migrated to the current schema and renamed into
place whole, and only then deletes them from the host's store. The runner's outbox counters stay, so a runner that runs
here again never numbers a fact at or below one its hub already holds. A runner the host file no longer declares is
named by its id. The archive is an ingest source
([Ingesting a runner from another store](#ingesting-a-runner-from-another-store)). It refuses as the whole-store reset
does.

`blizzard runner status` also names the runner's tenant beside its id, as `tenant=<name> (<tenant_id>)`, once a hub that
names it has answered.
