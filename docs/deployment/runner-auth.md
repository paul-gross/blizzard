# Runner authentication

Runner authentication is machine identity, a runner authenticating itself to the hub — distinct from human login
([human-auth.md](./human-auth.md)).

Every outbound runner-to-hub call — the reconciliation loop and the work-items proxy alike — attaches
`Authorization: Bearer` with the token the hub issued when it added the runner, and the hub takes the runner's identity
from that token alone. A fleet call with no token, or with one the hub never issued, is refused with a `401` under every
configuration: no runner joins without being added.

A worker's lease token, which it presents to the runner's own API, also authorizes its `POST /v1/traces` span export,
and under `harness_telemetry` its `POST /v1/metrics` and `POST /v1/logs` exports;
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
`BZ_HUB_TOKEN=<token>` line a runner's runtime-dir `.env` takes; there is no read-back. The runner's first registration
with that token resolves it to the id, and from then on the `name` its own `blizzard-runner.toml` declares replaces the
one `add` was given ([remote-runner.md](../remote-runner.md#point-it-at-the-hub) owns naming).

`blizzard-runner init <dir>` adds the runner and writes its token in one step, at the hub its config names, which
`--hub <url>` or else `BZ_HUB_URL` sets on a fresh config. With no token, it adds the runner under the config's `name`.
On a hub with `auth.mode = "none"` that needs no sign-in; on one that requires it, the add runs under your
`blizzard hub login` session for that hub, so it takes an operator holding `runner:add`, and with no session init stops
and says so. With a token, init first asks the hub whose it is (`GET /api/fleet/identity`, under the runner's own token
and with no side effects) and acts on the answer:

| The hub answers      | `init`                                                                                                                          |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| the token's runner   | keeps the token and adds nothing                                                                                                |
| an unknown token     | stops, adds nothing, and leaves `.env` untouched, naming the hub it asked                                                       |
| a revoked token      | stops, naming `hub runner enroll <id>` — rotation keeps the runner's id, and an add would not                                   |
| a retired runner     | stops, naming `hub runner reinstate <id>` then `enroll <id>` — retiring revoked the token, and an add would undo the retirement |
| nothing, or an error | fails and adds nothing, so an unreachable hub never gains a duplicate runner                                                    |

Both stops on a revoked token or a retired runner say where the new token goes: the runtime dir's `.env`, or the
variable that supplied the token init presented, which overrides that file.

An unknown token means one of two things, and only you can tell which. The hub's data was deliberately reset — a
development hub started over — and the runner's id went with it: re-run init with `--allow-readd`, and it adds a fresh
runner and replaces the token. Or the config names the wrong hub, one that never added this runner: re-adding there
would mint a runner at that hub and overwrite the only token the runner holds for its own, so fix `hub_url` instead.
Init never re-adds without the flag.

`blizzard-runner.toml`'s `token_env` (default `BZ_HUB_TOKEN`) names the variable carrying the token, never the secret
itself. Every runner verb reads it from the process environment first, then from the runtime dir's `.env`, which init
writes: it replaces only the token's line, keeps every other line in systemd `EnvironmentFile` syntax, writes the file
owner-only (`0600`) through an atomic rename, and so replaces a symlinked `.env` with a regular file. When init keeps a
token that a hand-written `.env` holds, it makes that file owner-only too, leaving its bytes as they are, or warns when
it cannot. The daemon reads only the token from that file. A token the process environment supplies wins, so when the
hub does not know it, init stops rather than write a replacement that would go unread.

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
`runner:retire` permission; `admin` and `superuser` hold both.
