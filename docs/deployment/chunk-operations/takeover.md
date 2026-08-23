# Taking over a parked session

`blizzard runner takeover <chunk_id> [--force]` continues a parked chunk's worker session interactively in your own
terminal.

## Running the verb

Run it as the service account, like every socket verb — [runner-doors.md](../runner-doors.md) owns that and the
`--dir`/`--runner-url` transports. On a split deployment, run it on the runner's own host: the wrong host refuses with
the not-held message even while the session is alive elsewhere.

Every refusal is a 409. `ChunkNotTakeable` when this runner does not hold the chunk, when no resumable session sits
behind its most recent lease, or when a takeover is already open; `LiveWorkerConflict` when the chunk has a live worker
attempt — pass `--force` to supersede the attempt instead of refusing; and `SubmissionPending` when, even under
`--force`, the attempt has already submitted — let it land, then `requeue`. The check is against the runner's actual
held session state, never the escalation's composed commands — a takeover can succeed against an escalation carrying
neither.

## What the daemon does

It records a takeover fact with the daemon first — so no loop step can respawn or judge the held session — then execs
the harness's resume command as your terminal's child, marking the takeover ended on exit, even on Ctrl-C. Opening a
takeover mints a fresh lease capability token, invalidating the previous one; but it is the open takeover fact, not a
fresh lease, that authorizes the verbs: a worker verb's lease resolves as either that lease's own activeness or an open
takeover naming it, so the referenced lease — usually already closed, ordinary for a parked or escalated chunk — serves
unchanged in id, node, and epoch.

The daemon supplies a bounded environment — the lease's `BLIZZARD_*` identity vars plus its own `PATH` and `HOME` —
layered over your terminal's; nothing more leaves the daemon, and the terminal environment underneath carries an
operator caveat owned by [worker-spawn.md](../worker-spawn.md). Under it, the session's `blizzard runner` verbs
(`attach`, `ask`, `artifact`) reach the runner, and the bare `blizzard` binary resolves to the deployment's venv. The
exec reasserts `harness_permission_mode` from `blizzard-runner.toml` — scaffold default `bypassPermissions`, per-tool
approval prompts disabled exactly as for the daemon-spawned worker; set another mode, or empty to omit the flag, to make
attended sessions prompt.

A taken-over session installs no heartbeat or session-end hooks: quitting records no done-signal against the lease, and
liveness stays a daemon-spawned-worker concern. The takeover ends when you exit — the CLI PATCHes it closed — and the
hub closes it when the chunk reaches a terminal status; the end-PATCH is idempotent, so a session stopped from the board
mid-takeover exits cleanly.

## The verb versus the raw resume string

For a runner-composed escalation the takeover verb, not the escalation record's raw resume string, is the supported way
in; `blizzard runner status` prints the raw string deliberately unchanged. The raw string resumes the transcript with
neither the permission mode nor the identity env: it runs at the harness's interactive permission default, and its
`blizzard runner` verbs cannot reach the runner — read and edit only. The board renders the wrapped verb as the primary
copyable command; the raw string is demoted to a collapsed unwrapped-fallback disclosure, present only when the
escalation carries one.

Which commands an escalation carries, and whether its session is reachable through takeover at all, is owned by
blizzard-context's
[`domain/humans/escalation.md`](https://github.com/paul-gross/blizzard-context/blob/master/domain/humans/escalation.md).

## When no runner can enter

Only when no runner can enter the session does resolving an escalation mean acting on the chunk directly — reading its
bounce history or migration guidance — and requeuing. For work finished outside the fleet, stop the chunk instead,
closing the escalation with it — [control-verbs.md](../control-verbs.md) owns the verb.
