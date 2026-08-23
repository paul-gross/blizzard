# Taking over a parked session

`blizzard runner takeover <chunk_id>` continues a parked chunk's worker session interactively in your own terminal: it
records a takeover fact with the daemon first, so no loop step can respawn or judge the session while you hold it, then
execs the harness's resume command as your terminal's child, and marks the takeover ended when you exit, even on Ctrl-C.
Opening a takeover mints a fresh lease capability token, invalidating the previous one.

Run takeover as the service account, like every socket verb; [runner-doors.md](../runner-doors.md) owns that and the
`--dir`/`--runner-url` transports. On a split deployment run takeover on the runner's own host first: the wrong host
refuses with the not-held message even while the session is alive elsewhere.

The daemon hands the takeover a bounded environment — the lease's `BLIZZARD_*` identity vars plus its own `PATH` and
`HOME` — layered over your terminal's, so the session's `blizzard runner` verbs (`attach`, `ask`, `artifact`) reach the
runner and the bare `blizzard` binary resolves to the deployment's venv; the rest of your shell stays untouched and
nothing more leaves the daemon. The exec reasserts `harness_permission_mode` from `blizzard-runner.toml` — scaffold
default `bypassPermissions`, meaning per-tool approval prompts are disabled exactly as for the daemon-spawned worker;
set another mode, or empty to omit the flag, if your deployment wants attended sessions prompted.

What authorizes the session's verbs is the open takeover fact itself, not a fresh lease: the reference lease it names is
very often already closed — the ordinary shape for a parked or escalated chunk — and the daemon resolves a worker verb's
lease as that lease's own activeness or an open takeover naming it, so the verbs work against the same closed lease
record, unchanged in id, node, and epoch. A taken-over session installs no heartbeat or session-end hooks: quitting it
must not record a done-signal against the lease, so liveness reporting stays a daemon-spawned-worker concern.

A takeover ordinarily ends when you exit and the CLI's own cleanup PATCHes it closed, but the hub can end it too: a
chunk reaching a terminal status while a takeover is open has the takeover fact closed by PULL on its next tick; the
end-PATCH is idempotent, so a session stopped from the board mid-takeover still exits cleanly rather than erroring.

takeover checks the runner's actual held session state, never the escalation's composed commands, so it can succeed
against an escalation carrying neither; it refuses with `ChunkNotTakeable` when this runner does not hold the chunk, no
resumable session sits behind its most recent lease, or a takeover is already open.

For a runner-composed escalation the takeover verb, not the escalation record's raw resume string, is the supported way
in: `blizzard runner status` still prints the raw string deliberately unchanged, and the board renders the wrapped verb
as the primary copyable command, demoting the raw string to a collapsed unwrapped-fallback disclosure present only when
the escalation carries one. The raw string resumes the transcript but carries neither the permission mode nor the
identity env: pasted into a bare terminal it runs at the harness's interactive permission default, and its
`blizzard runner` verbs cannot reach the runner — it can only read and edit.

## Resolving an escalation

Which commands a given escalation carries, and whether its session is still reachable through takeover at all, is a
domain fact owned by blizzard-context's
[domain/humans/escalation.md](https://github.com/paul-gross/blizzard-context/blob/master/domain/humans/escalation.md).
Only when no runner can enter the session does resolving an escalation mean acting on the chunk directly — reading its
bounce history or migration guidance — and requeuing; when the work was finished outside the fleet entirely, stop the
chunk instead, which closes the escalation with it ([control-verbs.md](../control-verbs.md)).
