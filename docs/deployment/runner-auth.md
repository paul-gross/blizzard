# Runner authentication

Runner authentication is machine identity, a runner authenticating itself to the hub — distinct from human login
([human-auth.md](./human-auth.md)).

Every outbound runner-to-hub call — the reconciliation loop and the work-items proxy alike — attaches
`Authorization: Bearer` with the runner's enrolled token; an unenrolled runner attaches nothing, and `runner_auth_mode`
decides whether the hub tolerates it.

Two independent rollout flags, scaffolded into `blizzard-hub.toml` by `hub init` and defaulting to `warn`, gate the
defenses: `runner_auth_mode` requires every fleet-router call's bearer token to resolve to a known runner identity, and
`route_token_mode` requires the per-acquisition route capability token on every chunk-scoped write. Under `warn` a
missing, invalid, or mismatched token is logged and the call proceeds; under `enforce` `runner_auth_mode` rejects with
401/403 and `route_token_mode` rejects the write as a semantic failure — a fresh or upgraded hub keeps working
unauthenticated until an operator deliberately tightens them.

One route ignores `runner_auth_mode`: a runner reading back its own shipped transcript segments
(`GET /api/fleet/chunks/{chunk_id}/transcript-segments`) is gated by that route's own always-raising ownership check,
refusing an unresolved or wrong-runner token even under `warn`.

## Enrollment

Enrollment requires prior registration: a runner registers itself with the hub on its own pull, and
`blizzard hub runner enroll <runner_id>` 404s on an unknown id — a deliberate act on a runner the fleet knows, not
trust-on-first-use. `enroll` mints the runner's bearer token — or rotates it when run again — and prints the plaintext
exactly once; there is no read-back, only rotation.

`blizzard-runner.toml`'s `token_env` (default `BZ_HUB_TOKEN`) names the environment variable carrying the enrolled
token, never the secret itself; the secret goes in the runner's env file (the systemd unit's `EnvironmentFile`), read
once at config load.

## Rollout order

Start the runner once so it registers; enroll it; install the token in the runner's runtime env file under the variable
its `token_env` key names; flip `runner_auth_mode` to `enforce` and restart the hub once every runner carries an
enrolled token; flip `route_token_mode` to `enforce` only after outbound buffers carrying pre-upgrade token-less facts
have drained — `warn` already covers that window, so there is no separate grace period.
