"""Operator-facing verbs that reach the local ``RunnerDaemon``: status, the pause brake, takeover, requeue, selftest."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass

import click

from blizzard.runner.cli.daemon import RunnerDaemon
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR

# The operator's TCP door onto the local API (issue #43) — the override for when the socket is not
# the right address. `BZ_*` is the operator's config namespace, distinct from the worker's
# spawn-injected `BLIZZARD_*` one, which `worker_call` owns.
ENV_LOCAL_API_URL = "BZ_RUNNER_URL"
# Each `selftest` poll is a machine-local read of already-computed state, so a short interval is free.
_SELFTEST_POLL_INTERVAL = 0.2
# A CLI-side backstop above the server's own authoritative run budget, so the CLI never spins forever
# against a runner that cannot reach that code.
_SELFTEST_POLL_TIMEOUT = 600.0


def _set_local_paused(*, paused: bool, by: str, directory: str, runner_url: str | None) -> None:
    """PATCH the runner singleton's own pause brake — the declarative pattern applied locally."""
    with RunnerDaemon.reach("pause" if paused else "start", directory, runner_url) as daemon:
        view = daemon.patch("/api/runner", json_body={"paused": paused, "by": by}).json()
    if paused:
        click.echo(f"runner {view['runner_id']} is now locally paused — it starts no new workers")
        if view.get("hub_paused"):
            click.echo(
                f"note: it is also paused at the hub — `blizzard hub runner resume {view['runner_id']}` clears that one"
            )
        return
    click.echo(f"runner {view['runner_id']} is no longer locally paused")
    if view.get("hub_paused"):
        click.echo(
            f"note: it stays paused at the hub — clear that with `blizzard hub runner resume {view['runner_id']}`"
        )


@dataclass(frozen=True)
class SessionLabel:
    """A parked session's identity as a trailing clause — ``"  session=code (opus, high)"``.

    Empty when the escalation carries none of the three (issue #144), so a bare line reads as
    "not recorded" rather than inventing one."""

    escalation: dict

    @property
    def text(self) -> str:
        pool = self.escalation.get("session_name")
        config = ", ".join(str(v) for v in (self.escalation.get("model"), self.escalation.get("effort")) if v)
        if not pool and not config:
            return ""
        if not pool:
            return f"  session=({config})"
        return f"  session={pool}" + (f" ({config})" if config else "")


@click.command()
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
@click.option(
    "--runner-url",
    "runner_url",
    default=None,
    envvar=ENV_LOCAL_API_URL,
    help="Runner local API over TCP (overrides $BZ_RUNNER_URL).",
)
def status(directory: str, runner_url: str | None) -> None:
    """The machine-local view: capacities, held environments, open asks, escalations, open takeovers
    (issue #51). Every section is this runner's own local read, so the view renders fully with the
    hub unreachable; hub reachability is itself reported, not assumed."""
    with RunnerDaemon.reach("status", directory, runner_url) as daemon:
        view = daemon.get("/api/runner").json()
        leases_resp = daemon.get("/api/leases")
        envs_resp = daemon.get("/api/environments")
        asks_resp = daemon.get("/api/asks", params={"open": "true"})
        escalations_resp = daemon.get("/api/escalations")
        takeovers_resp = daemon.get("/api/takeovers")

    click.echo(f"runner {view['runner_id']}  workspace={view['workspace_id']}")
    pause = view["pause"]
    brakes = [name for name, on in (("local", pause["local"]), ("hub", pause["hub"])) if on]
    brake_state = f"paused [{'+'.join(brakes)}]" if pause["effective"] else "running"
    click.echo(f"  {brake_state}")
    cap = view["capacities"]
    click.echo(f"  capacity: {cap['used']}/{cap['max_agents']} used, {cap['free']} free")
    hub = view["hub"]
    reachability = "reachable" if hub["reachable"] else "unreachable"
    contact = hub["last_contact_at"] or "never"
    click.echo(f"  hub: {reachability} (last contact {contact}), {hub['buffer_depth']} fact(s) buffered")
    click.echo(f"  last tick: {view['last_tick_at'] or 'never'}")

    leases = [lease for lease in leases_resp.json().get("items", []) if lease.get("state") != "closed"]
    click.echo(f"\nleases ({len(leases)}):")
    for lease in leases:
        click.echo(f"  {lease['lease_id']}  {lease['state']:<12} chunk={lease['chunk_id']} node={lease['node_name']}")

    # `GET /api/environments` carries the full configured pool (issue #106); this section
    # is the *held*-environments view, so unused pool slots (chunk_id null) are filtered out.
    envs = [env for env in envs_resp.json().get("items", []) if env.get("chunk_id") is not None]
    click.echo(f"\nheld environments ({len(envs)}):")
    for env in envs:
        click.echo(f"  {env['environment_id']}  chunk={env['chunk_id']}  held since {env['held_since']}")

    asks = asks_resp.json().get("items", [])
    click.echo(f"\nopen asks ({len(asks)}):")
    for ask in asks:
        opts = f"  [{'|'.join(ask.get('options') or [])}]" if ask.get("options") else ""
        click.echo(f"  {ask['question_id']}  (chunk {ask['chunk_id']}): {ask['question']}{opts}")

    escalations = escalations_resp.json().get("items", [])
    click.echo(f"\nescalations ({len(escalations)}):")
    for esc in escalations:
        click.echo(
            f"  chunk {esc['chunk_id']}  node={esc['node_id']}  since {esc['closed_at']}{SessionLabel(esc).text}"
        )
        click.echo(f"    resume: {esc['resume_command']}")

    takeovers = takeovers_resp.json().get("items", [])
    click.echo(f"\nopen takeovers ({len(takeovers)}):")
    for tko in takeovers:
        click.echo(f"  chunk {tko['chunk_id']}  takeover={tko['takeover_id']}  held since {tko['held_since']}")


@click.command()
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
@click.option(
    "--runner-url",
    "runner_url",
    default=None,
    envvar=ENV_LOCAL_API_URL,
    help="Runner local API over TCP (overrides $BZ_RUNNER_URL).",
)
@click.option("--by", "by", default="operator", help="Who is pausing (recorded on the fact).")
def pause(directory: str, runner_url: str | None, by: str) -> None:
    """Declarative control: pause this runner — it starts no new workers (issue #45). This runner's
    **own** brake, a pure client of its local API, so it works with the hub unreachable: it blocks
    every spawn site and defers both the kill of a stalled worker and escalation at an exhausted retry
    budget. No retry is consumed, and a live worker is left alone — this is not a drain. Distinct from
    the hub's brake, and each is cleared where it was set."""
    _set_local_paused(paused=True, by=by, directory=directory, runner_url=runner_url)


@click.command()
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
@click.option(
    "--runner-url",
    "runner_url",
    default=None,
    envvar=ENV_LOCAL_API_URL,
    help="Runner local API over TCP (overrides $BZ_RUNNER_URL).",
)
@click.option("--by", "by", default="operator", help="Who is starting it (recorded on the fact).")
def start(directory: str, runner_url: str | None, by: str) -> None:
    """Declarative control: clear this runner's own pause brake — it resumes spawning (issue #45).

    The counterpart to ``blizzard runner pause``, and local in the same way. It clears only
    the local brake: a runner also paused at the hub stays paused until ``blizzard hub
    runner resume <runner_id>`` clears that one too."""
    _set_local_paused(paused=False, by=by, directory=directory, runner_url=runner_url)


@click.command()
@click.argument("chunk_id")
@click.option("--force", is_flag=True, default=False, help="Supersede a live worker attempt instead of refusing.")
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
@click.option(
    "--runner-url",
    "runner_url",
    default=None,
    envvar=ENV_LOCAL_API_URL,
    help="Runner local API over TCP (overrides $BZ_RUNNER_URL).",
)
def takeover(chunk_id: str, force: bool, directory: str, runner_url: str | None) -> None:
    """Take over a parked chunk: exec the interactive resume command in this terminal (issue #52). The
    takeover fact is recorded before anything else runs, so no loop step can respawn or judge the
    session while it is open; the lease token travels only in the response body and the exec, never
    printed. ``--force`` supersedes a live worker attempt instead of refusing. The end-PATCH runs in a
    ``finally`` around the child, so a stranded open takeover cannot outlive an interrupted session."""
    with RunnerDaemon.reach("takeover", directory, runner_url) as daemon:
        resp = daemon.send("post", f"/api/chunks/{chunk_id}/takeovers", json_body={"force": force})
        if resp.status_code == 409:
            raise click.ClickException(f"takeover: {resp.json().get('detail', 'chunk is not takeable')}")
        resp.raise_for_status()
        view = resp.json()
        click.echo(f"taking over chunk {chunk_id} in {view['workdir']}")
        try:
            # The takeover env (issue #258), layered over the terminal env: the forwarded
            # vars deliberately WIN over the terminal's own, and carry the lease token.
            child_env = {**os.environ, **view.get("env", {})}
            exit_code = subprocess.call(view["command"], shell=True, cwd=view["workdir"], env=child_env)
        finally:
            daemon.patch(f"/api/chunks/{chunk_id}/takeovers/{view['takeover_id']}")
    if exit_code != 0:
        raise SystemExit(exit_code)


@click.command()
@click.argument("chunk_id")
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
@click.option(
    "--runner-url",
    "runner_url",
    default=None,
    envvar=ENV_LOCAL_API_URL,
    help="Runner local API over TCP (overrides $BZ_RUNNER_URL).",
)
def requeue(chunk_id: str, directory: str, runner_url: str | None) -> None:
    """Hand a needs_human chunk back to the fleet: a fresh attempt at its current node (issue #53).
    Appends the fact that clears the chunk's local needs_human hold; the next FILL spawns a fresh
    attempt — new session, new lease, fresh epoch — at the current node. The route is never released
    and the chunk never re-enters the hub's queue. Refused ``409`` while its takeover is still open,
    or while it is not parked needs_human."""
    with RunnerDaemon.reach("requeue", directory, runner_url) as daemon:
        resp = daemon.send("post", f"/api/chunks/{chunk_id}/requeues")
        if resp.status_code == 409:
            raise click.ClickException(f"requeue: {resp.json().get('detail', 'chunk is not requeueable')}")
        resp.raise_for_status()
    click.echo(f"requeued chunk {chunk_id} — a fresh attempt will spawn at its current node")


@click.command()
@click.argument("coding_harness")
@click.option(
    "--dir",
    "directory",
    default=DEFAULT_DIR,
    envvar=ENV_RUNNER_DIR,
    help="Runner runtime directory (overrides $BZ_RUNNER_DIR).",
)
@click.option(
    "--runner-url",
    "runner_url",
    default=None,
    envvar=ENV_LOCAL_API_URL,
    help="Runner local API over TCP (overrides $BZ_RUNNER_URL).",
)
def selftest(coding_harness: str, directory: str, runner_url: str | None) -> None:
    """Adapter-drift canary before an unattended period (issue #54): exercises CODING_HARNESS against a
    throwaway scratch repo — spawn with a pre-assigned session id, a trivial edit+commit, verdict
    elicitation, an automated follow-up resume, and resume-command composition — touching no chunk,
    lease, environment, or hub. Posts the run, polls it, prints each check, exits non-zero on failure."""
    with RunnerDaemon.reach("selftest", directory, runner_url) as daemon:
        resp = daemon.send("post", "/api/selftests", json_body={"harness": coding_harness})
        if resp.status_code == 422:
            raise click.ClickException(resp.json().get("detail", "unknown coding harness"))
        resp.raise_for_status()
        run = resp.json()
        deadline = time.monotonic() + _SELFTEST_POLL_TIMEOUT
        while run["status"] == "running":
            if time.monotonic() > deadline:
                raise click.ClickException(
                    f"selftest {run['id']} did not finish within {_SELFTEST_POLL_TIMEOUT:g}s — the runner may be wedged"
                )
            time.sleep(_SELFTEST_POLL_INTERVAL)
            run = daemon.get(f"/api/selftests/{run['id']}").json()

    for check in run["checks"]:
        mark = "PASS" if check["passed"] else "FAIL"
        click.echo(f"[{mark}] {check['name']}: {check['detail']}")
    if run["status"] != "passed":
        if run.get("error"):
            click.echo(f"selftest error: {run['error']}", err=True)
        click.echo(f"selftest {run['id']} FAILED for {coding_harness}", err=True)
        raise click.exceptions.Exit(1)
    click.echo(f"selftest {run['id']} passed for {coding_harness}")
