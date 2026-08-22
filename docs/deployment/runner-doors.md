# Runner doors

The runner daemon serves one API on two listeners — same app, same routes; the door depends on who the client is.

## The two listeners

The unix socket `runner.sock`, mode 0600 in the runtime dir, is the CLI local verbs' door (`runner pause`, `start`,
`takeover`), addressed via `--dir` or `$BZ_RUNNER_DIR` — no port, no config file read. The TCP port (8431 by default) is
the door for the runner's web panel (same-origin `/api/*` on the page's own host) and for worker hooks such as heartbeat
and ask, which address it via the spawn-injected `BLIZZARD_RUNNER_URL`. The TCP listener exists because a browser cannot
open a unix socket; the socket exists so operator controls depend on no port, filesystem permissions being their access
control.

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
next host start is clean: it clears a socket nothing is serving, and refuses to start beside one still live, the store
being single-writer.

With no daemon running, client verbs report that rather than reading the store behind its back — a client verb reaches
the store only through the daemon that owns it. The writing offline maintenance verbs — `migrate`, `tick`,
`transcript backfill` and `reship` ([transcripts.md](./transcripts.md)) — open the store directly and run with the
daemon stopped, enforced by their own refusal; the read-only `prompt status`, `diff`, and `install` open it for one
query and need no refusal, the single-writer constraint binding writers only.

## The web panel and its stream

The TCP door also carries `GET /api/events/stream`, a `text/event-stream` route deliberately absent from the OpenAPI
spec (no generated client calls it): it publishes lease, ask, escalation, takeover, environment, and fact changes the
instant they happen, replaying from `Last-Event-ID` on reconnect like the hub's own stream
([observability.md](./observability.md)). The panel renders leases, environments, asks, escalations, takeovers, and
facts live off that stream; dashboard and leases reads keep a one-minute poll only as a backstop, and the session read
has no poll — a stream 401 routes into the same recovery seam as any read's 401.

The panel's top-bar Pause/Resume control issues the identical `PATCH /api/runner` the CLI's local verbs use — the same
write path, not a second one.

## The two brakes

The local brake means start no processes on this machine — no new claims, no restart-resume, no requeue respawn, no
judging an exited worker (judging resumes a session); the hub brake (`blizzard hub runner pause`) only stops new claims,
in-flight chunks running on. Neither brake is a drain: a running worker is left alone, and every lease, route, and retry
budget the local brake defers picks up exactly where it left off once cleared, unless an operator moved the chunk —
`blizzard-runner pause --help` carries the full contract.

`runner pause` and `runner start` are pure clients of the runner's API that never contact the hub, so they keep working
while it is unreachable. Each brake clears only where it was set: `runner start` locally, `blizzard hub runner resume`
at the hub. Because the panel toggle can only move the local brake, the panel shows a Paused-by-hub badge whenever the
hub brake is set, so an off toggle still explains why the runner is not filling; clearing the hub brake stays hub-only.

One non-operator trigger engages the identical local brake: a configured spend ceiling ([spend.md](./spend.md)) — so a
runner can read `[paused: local]` with no `runner pause` ever issued; clearing is the same explicit `runner start` or
panel Resume, and `hub status` names the ceiling reason.
