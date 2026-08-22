# Runner authentication

This is **machine identity** — a runner authenticating itself to the hub — distinct from the **human login** plane
([Human authentication](./human-auth.md)), which authenticates an operator to the hub's own web/API surface.

Two independent rollout flags gate the fleet's runner-identity and route-capability defenses, both scaffolded into
`blizzard-hub.toml` by `blizzard hub init`, both defaulting to `warn`:

| Flag               | Guards                                                                           | `warn` (default)                                                  | `enforce`                        |
| ------------------ | -------------------------------------------------------------------------------- | ----------------------------------------------------------------- | -------------------------------- |
| `runner_auth_mode` | every fleet-router call's bearer token resolves to a known runner identity       | logs a missing/invalid/mismatched token and lets the call proceed | rejects it (401/403)             |
| `route_token_mode` | the per-acquisition route capability token presented on every chunk-scoped write | logs a missing/mismatched route token and lets the write proceed  | rejects it as a semantic failure |

They are independent on purpose — a fleet can flip one on before the other — and neither has any effect while `warn`; a
fresh deploy or an upgraded hub keeps working unauthenticated until an operator deliberately tightens them.

**One route ignores `runner_auth_mode` outright.** A runner reading back its own shipped transcript segments
(`GET /api/fleet/chunks/{chunk_id}/transcript-segments`) is gated on that route's own always-raising ownership check
instead: it refuses (401/403) a caller whose token doesn't resolve or names a different runner than the segments' owner,
regardless of the flag's `warn`/`enforce` setting — unlike the rest of the fleet router, where `warn` leaves an
unresolved or mismatched token to proceed.

**Enrollment requires the runner to have registered first.** A runner registers itself with the hub on its own pull;
`blizzard hub runner enroll <runner_id>` 404s naming the unknown id until that has happened at least once. Enrollment is
a deliberate operator act on a runner the fleet already knows, not a trust-on-first-use grant to a name nobody has
registered yet.

The rollout sequence, in order:

1. Start the runner once so it registers with the hub.
2. `blizzard hub runner enroll <runner_id>` — mints (or, run again, rotates) the runner's bearer token and prints the
   plaintext exactly once; there is no way to read it back later, only to rotate it.
3. Install that token in the runner's own runtime env file (the systemd `EnvironmentFile`, or the shell env a
   manually-run runner inherits) under the variable its `token_env` config key names — see "The runner's outbound token"
   below.
4. Flip `runner_auth_mode` to `enforce` in `blizzard-hub.toml` and restart the hub, once every runner in the fleet
   carries an enrolled token.
5. Flip `route_token_mode` to `enforce` only after outbound buffers carrying pre-upgrade, token-less facts have drained
   — `warn` already covers that window, so there is no separate grace period to wait out beyond it.

## The runner's outbound token

`blizzard-runner.toml`'s `token_env` (default `BZ_HUB_TOKEN`) names the environment variable carrying the runner's
enrolled bearer token — never the secret itself, mirroring the
[`[[work_source]] token_env` indirection](./work-sources.md#credential-indirection). The secret goes in the runner's
runtime env file (e.g. `/etc/blizzard/runner.env` under the systemd layout, declared as that unit's `EnvironmentFile`),
read once at config load. Every outbound runner→hub call — the reconciliation loop's `httpx.Client` and the work-items
proxy alike — attaches it as `Authorization: Bearer <token>`; an unenrolled runner (or one whose env file has not been
updated yet) attaches nothing, and `runner_auth_mode` above decides whether the hub tolerates that.
