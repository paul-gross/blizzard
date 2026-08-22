# The runner's two doors

The runner daemon serves one API on two listeners, and which one you address depends on who you are:

| Client                                                                    | Door                                         | How it addresses it                                          |
| ------------------------------------------------------------------------- | -------------------------------------------- | ------------------------------------------------------------ |
| the CLI's local verbs (`runner pause`, `runner start`, `runner takeover`) | `runner.sock`, mode 0600, in the runtime dir | `--dir` (or `$BZ_RUNNER_DIR`) — no port, no config file read |
| the runner's web app in a browser                                         | the TCP port (`8431` by default)             | same-origin `/api/*` on the page's own host                  |
| worker hooks (`heartbeat`, `ask`, …)                                      | the TCP port                                 | `BLIZZARD_RUNNER_URL`, injected into the spawn               |

Same app, same routes — two doors, not two APIs. A browser cannot open a unix socket, which is why the TCP listener
exists; the socket exists because the operator's controls should not depend on a port, and filesystem permissions are
their access control.

The TCP door also carries `GET /api/events/stream` (issue #317) — a `text/event-stream` route in the same human-facing
lane as the web app, deliberately absent from the OpenAPI spec since no generated client calls it. It publishes a lease,
ask, escalation, takeover, environment, or fact change the instant it happens, replaying from a `Last-Event-ID` on
reconnect exactly as the hub's own stream does; see
[the hub's operational event log](./observability.md#operational-visibility--the-event-log) for the hub side of the same
mechanism. The runner's own web panel is its one subscriber (see below).

**Run the local verbs as the service account.** The socket is mode 0600 and the unit runs as `blizzard`, so the
filesystem access control above is doing its job: another account — including root's shell habits — is not the owner,
and the verb fails with `EACCES`. Use the same `sudo -u` form the install steps use:

```bash
sudo -u blizzard /opt/blizzard/venv/bin/blizzard-runner pause --dir /var/lib/blizzard/runner
sudo -u blizzard /opt/blizzard/venv/bin/blizzard-runner start --dir /var/lib/blizzard/runner
```

The board's copyable wrapped takeover command (issue #251; see
[Taking over a parked session](./chunk-operations.md#taking-over-a-parked-session--blizzard-runner-takeover)) supplies
that `--dir` for you, but nothing else here:

- **Not the service account** — the pasted command still needs a shell already set up exactly like the `sudo -u` form
  above, on the runner's own host.
- **Not the venv's `blizzard` binary path.**
- **Not the host itself** — `--dir` names a path on the **runner's** host, while the board is served by the **hub**, so
  on a split deployment ([`docs/remote-runner.md`](../remote-runner.md)) a pasted command can fail outright by landing
  on the wrong machine entirely, not just the wrong account or binary path.

`--runner-url` (or `$BZ_RUNNER_URL`) points a local verb at the TCP door instead — for a shell that cannot see the
runtime dir, or cannot open the socket. Passing both `--dir` and `--runner-url` explicitly is an error; an explicit flag
beats either variable, and if both arrive from the environment the socket wins (the default transport). Note the two are
different namespaces: `$BZ_RUNNER_URL` is this operator setting, while `BLIZZARD_RUNNER_URL` in the table above is
spawn-injected worker identity the runner mints per worker — setting one does not affect the other.

`runner pause` and `runner start` are pure clients of this API and never contact the hub, so they keep working while it
is unreachable. They set the runner's **own** brake, which means something different from
`blizzard hub runner pause <runner_id>`: the hub brake still just stops new claims (in-flight chunks always run on); the
runner's own brake means "start no processes on this machine" — no new claims, but also no restart-resume, no requeue
respawn, and no judging a worker that exits while it's on, since judging one resumes its session. Nothing is lost either
way: a live worker already running is left alone (this is not a drain), and every lease, route, and retry budget the
brake defers is picked up once it clears — exactly where it left off, unless an operator moved the chunk meanwhile — see
`blizzard-runner pause --help` for the full contract. Each brake is cleared only where it was set — `runner start`
locally, `blizzard hub runner resume` at the hub.

The panel's leases, environments, asks, escalations, takeovers, and facts render live: new events fan out over the
runner's own SSE spine (`/api/events/stream`, issue #317), so an open panel updates without polling — the parallel of
what the board's Events tab does off the hub's stream, above. The panel's own dashboard/leases reads keep a one-minute
poll as a backstop against a dropped frame rather than as the primary signal, and the session read carries no poll of
its own: a stream `401` routes into the same recovery seam a `401` from any other read does.

The runner's own web panel (issue #133) carries the same local brake as a second local door: a Pause/Resume control in
its top bar issues the identical `PATCH /api/runner` the CLI's local verbs use, so a click there is
`runner pause`/`runner start` by another name, not a second write path. Because the toggle can only ever move the local
brake, the panel also renders an explicit **Paused by hub** badge whenever the hub's own brake is set — an operator
whose local toggle reads "off" then still sees why the runner is not filling, rather than the toggle looking broken.
Clearing the hub's own brake stays hub-only, though — `blizzard hub runner resume`, never something reachable from this
panel.

The local brake has one **non-operator** trigger too: a configured runner spend ceiling engages this same brake
automatically when the fleet's rolling-window spend crosses it (see [Bounding fleet spend](./spend.md)). It is the
identical brake — same "start no processes on this machine" semantics, live workers left to finish — so a runner can
come back `[paused: local]` with no `runner pause` ever issued. Clearing it is always an explicit operator action, never
automatic — `runner start` at the CLI, or the runner panel's Resume control, the same two doors that clear a hand-issued
pause. `blizzard hub status` names the reason on a ceiling-engaged runner so you can tell it apart from a hand-issued
pause.

With no daemon running, the verbs report that rather than reading the store behind its back — a **client** verb reaches
the store only through the daemon that owns it. Two kinds of verb open the store directly instead. The **writing**
offline maintenance verbs are therefore run with the daemon *stopped*: `migrate`, `tick`, and the two transcript verbs
[`transcript backfill` and `transcript reship`](./transcripts.md#shipping-transcript-content-to-the-hub--the-outbound-lane-off-by-default),
whose own refusal enforces it. The **read-only** `prompt status`, `prompt diff`, and `prompt install` open it for one
query — whether a workspace-prompt override stands — and need no such refusal, because the single-writer constraint the
refusal protects binds writers only. What you see from a client verb depends on how the daemon left:

| How it stopped             | On disk                               | What a local verb reports                                                 |
| -------------------------- | ------------------------------------- | ------------------------------------------------------------------------- |
| `systemctl stop` / SIGTERM | the socket is unlinked on the way out | `no runner daemon is serving at …` — start one                            |
| `kill -9`, OOM, reboot     | the socket file is left behind        | a connection error against that path — nothing is listening on the corpse |

Either way the next `host` start is clean: it clears a socket nothing is serving, and refuses to start beside one that
is still live (the store is single-writer).
