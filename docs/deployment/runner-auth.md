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

`blizzard-runner init <dir>` adds the runner and writes its token in one step, at the hub its config names, which
`--hub
<url>` or else `BZ_HUB_URL` sets on a fresh config. With no token, it adds the runner under the config's `name`.
On a hub with `auth.mode = "none"` that needs no sign-in; on one that requires it, the add runs under your
`blizzard hub
login` session for that hub, so it takes an operator holding `runner:add`, and with no session init stops
and says so. It adds the runner in the tenant `--tenant <tenant_id>` names, by id, never by name; without the flag, in
the only tenant you may enter, and when you may enter several it stops, listing them, ids beside names. With a token,
init first asks the hub whose it is (`GET /api/fleet/identity`, under the runner's own token and with no side effects)
and acts on the answer:

| The hub answers                                             | `init`                                                                                                                                         |
| ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| the token's runner                                          | keeps the token and adds nothing                                                                                                               |
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

Before it re-adds, init sets the old runner's store aside under a timestamped name beside it and migrates a fresh one,
so the new runner never inherits the old one's leases and buffered facts. Nothing is deleted: the old store keeps them.
It refuses while the runner's daemon is running. A runner holding no token whose store still belongs to a runner stops
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

If the hub adds the runner but its token cannot be written, init exits naming
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
retire, revoke-token and enroll, and each runner row on it carries release. Release, retire and revoke-token each ask
for confirmation first. A retire the hub refuses shows the refusal and asks again before forcing it. Enroll shows the
new token once.

## A token that names another runner

At the head of every tick the runner asks the hub which runner its token belongs to (`GET /api/fleet/identity`) and
compares the answer with the runner its host file configures, the `[[runner]] id` the store is bound to. When they
differ — a wrong token, or a `hub_url` pointing at another hub — the runner refuses to drive its leases or deliver its
buffered facts, and checks again on the next tick, so putting things right resumes it with nothing lost. A hub that
cannot be reached, or that refuses the token, leaves the verdict as it stood: an outage never reads as a mismatch.

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

`blizzard runner status` also names the runner's tenant beside its id, as `tenant=<name> (<tenant_id>)`, once a hub that
names it has answered.
