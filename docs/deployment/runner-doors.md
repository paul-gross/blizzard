# Runner doors

The host daemon serves one API on two listeners — same app, same routes; the door depends on who the client is.

## The host's routes and each runner's

The host serves one origin. What belongs to the host answers at the root:

- `GET /api/health` — the host's version.
- `GET /api/ready` — the store's readiness, beside a `runners` list naming each runner the host holds, its id, name, and
  whether it is `serving` or `halted`.
- `/api/auth/*` — the panel's one sign-in.
- `GET /api/events/stream` — the host's one event stream.
- `/api/host/*` — the host's own reads: `runners` (each runner's brake, bindings, and agents in use, or a halted one's
  reasons), `workspaces`, `environments` (grouped by workspace, each held one naming its runner and project), and
  `subscriptions` (each naming the runners that spend it); and `workspaces/{workspace_id}/prompt`, the runtime
  workspace-prompt override (`GET`, `PUT`, `DELETE`), which answers `404` for a workspace the host does not declare.

Everything that belongs to one runner — its leases, dashboard, asks, control verbs, takeovers, traces, worker hooks, and
OTLP receiver — answers under `/runners/{runner_id}/`, keyed by the hub-minted id: `/runners/{runner_id}/api/...` and
`/runners/{runner_id}/v1/{traces,metrics,logs}`. An id the host does not hold answers `404`; a runner it holds halted
answers `503` naming its reasons. Nothing answers at the unprefixed paths a lone runner once served: an API path no
route matches answers a JSON `404`, never the panel. `openapi/runner.openapi.json` documents both halves in one
document, with `runner_id` a path parameter on every runner operation.

## The two listeners

The unix socket `host.sock`, mode 0600 in the host directory, is the CLI local verbs' door (`runner pause`, `start`,
`clear-floor`, `takeover`), addressed via `--dir` or `$BZ_RUNNER_DIR` — no port, no config file read. The TCP port (8431
by default) is the door for the host's web panel (same-origin on the page's own host) and for worker hooks such as
heartbeat and ask, which address their runner via the spawn-injected `BLIZZARD_RUNNER_URL`,
`http://<host>:<port>/runners/<runner_id>`. The TCP listener exists because a browser cannot open a unix socket; the
socket exists so operator controls depend on no port, filesystem permissions being their access control.

`--runner-url` (or `$BZ_RUNNER_URL`) points a local verb at the TCP door instead, for a shell that cannot see the
runtime dir or open the socket; both flags explicitly is an error, an explicit flag beats either variable, and both from
the environment means the socket wins as default transport. `$BZ_RUNNER_URL` and the spawn-injected
`BLIZZARD_RUNNER_URL` are different namespaces — operator setting versus per-worker identity — and setting one does not
affect the other.

Run the local verbs as the service account: the socket is 0600 and the unit runs as `blizzard`, so any other account —
root included — fails with EACCES; use the install steps' `sudo -u blizzard <venv>/bin/blizzard-runner` form.

The board's copyable wrapped takeover command supplies only `--dir` — not the service account, the venv binary path, or
the host: `--dir` names a path on the runner's host while the board serves from the hub, so on a split deployment a
pasted command can land on the wrong machine entirely.

## Socket lifecycle and the single-writer store

`systemctl stop` or SIGTERM unlinks the socket on the way out, so a client verb reports no daemon serving; `kill -9`,
OOM, or reboot leaves the socket file behind, so the verb reports a connection error against the corpse. Either way the
next host start is clean: it clears a socket nothing is serving, and refuses to start beside a live host — one holding
the directory's `data/host.lock` — or a pre-host daemon still answering on `runner.sock`, the store being single-writer.

With no daemon running, client verbs report that rather than reading the store behind its back — a client verb reaches
the store only through the daemon that owns it. The writing offline maintenance verbs — `migrate`, `tick`,
`transcript backfill` and `reship` ([transcripts.md](./transcripts.md)) — open the store directly and run with the
daemon stopped, enforced by their own refusal; the read-only `prompt status`, `diff`, and `install` open it for one
query and need no refusal, the single-writer constraint binding writers only. `prompt clear` is the one `prompt` verb
that is a client verb: it writes the store, so it goes through the daemon, at
`DELETE /api/host/workspaces/{workspace_id}/prompt` for the workspace the other `prompt` verbs resolve.

## Choosing the runner: `--runner`

`status`, `pause`, `start`, `takeover`, `requeue`, `selftest`, `traces status`, and `traces replay` each act on one of
the host's runners, which `--runner` names by name or by id. The verb resolves it against the running host's
`GET /api/host/runners`, over either door, and addresses that runner under its prefix. Omitted on a host of one runner,
it is that runner. On a host of several runners `--runner` is required: omitted, or naming none the host holds, the verb
fails naming the host's runners. A runner the host holds halted by its own configuration fails naming why; one its hub
halts is still served, so `status` reads it and names the halt. `clear-floor` takes `--runner` the same way, but only in
place of `--subscription`: it names exactly one of the two, so omitting both fails.

The offline verbs — `tick`, `transcript backfill` and `reship`, `harness status`, and the `prompt` verbs — read the host
file rather than the running host, and take `--runner` the same way: optional on a host of one runner, required on a
host of several. The `prompt` verbs use it only to pick the workspace — the runner's own — whose prompt they read or
clear; the override `prompt clear` drops is the host's, per workspace.

## The web panel and its stream

The TCP door also carries `GET /api/events/stream`, a `text/event-stream` route deliberately absent from the OpenAPI
spec (no generated client calls it): it publishes lease, ask, escalation, takeover, environment, and fact changes the
instant they happen, replaying from `Last-Event-ID` on reconnect like the hub's own stream (`?from=latest` instead joins
at the latest event, with no replay) ([observability.md](./observability.md)). Every runner-scoped frame carries its
runner's `runner_id`; an `environment-changed` frame is the host's and carries none. The panel renders leases,
environments, asks, escalations, takeovers, and facts live off that stream; dashboard and leases reads keep a one-minute
poll only as a backstop, and the session read has no poll — a stream 401 routes into the same recovery seam as any
read's 401.

The panel's top-bar Pause/Resume control issues the identical `PATCH /runners/{runner_id}/api/runner` the CLI's local
verbs use — the same write path, not a second one.

## The two brakes

The local brake means start no processes on this machine — no new claims, no restart-resume, no requeue respawn, no
judging an exited worker (judging resumes a session); the hub brake (`blizzard hub runner pause`) only stops new claims,
in-flight chunks running on. Neither brake is a drain: a running worker is left alone (bar a restart's fenced-out
worker, which the local brake still kills — [control-verbs.md](./control-verbs.md#restart)), and every lease, route, and
retry budget the local brake defers picks up exactly where it left off once cleared, unless an operator moved the chunk
— `blizzard-runner pause --help` carries the full contract.

`runner pause` and `runner start` are pure clients of the runner's API that never contact the hub, so they keep working
while it is unreachable. Each brake clears only where it was set: `runner start` locally, `blizzard hub runner resume`
at the hub. Because the panel toggle can only move the local brake, the panel shows a Paused-by-hub badge whenever the
hub brake is set, so an off toggle still explains why the runner is not filling; clearing the hub brake stays hub-only.

One non-operator trigger engages the identical local brake: a configured spend ceiling ([spend.md](./spend.md)) — so a
runner can read `[paused: local]` with no `runner pause` ever issued; clearing is the same explicit `runner start` or
panel Resume, and `hub status` names the ceiling reason.
