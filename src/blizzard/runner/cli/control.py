"""Operator-facing verbs that reach the local ``RunnerDaemon``: status, the pause brake, takeover, requeue, selftest."""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass

import click

from blizzard.foundation.credential_renewal import RenewalFailureReason, RenewalResult
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.roles import dto
from blizzard.foundation.subscription_miss import SampleMissReason
from blizzard.runner.cli.daemon import RunnerDaemon
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.cli.traces import harness_telemetry_lines
from blizzard.runner.subscriptions.credential_renewer import RENEWAL_FAILURE_TEXT
from blizzard.runner.subscriptions.subscription_sampler import MISS_REASON_TEXT

# The operator's TCP door onto the local API — the override for when the socket is not
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
        click.echo(f"runner {view['runner_name']} is now locally paused — it starts no new workers")
        if view.get("hub_paused"):
            click.echo(_hub_pause_note("is also", view.get("runner_id")))
        return
    click.echo(f"runner {view['runner_name']} is no longer locally paused")
    if view.get("hub_paused"):
        click.echo(_hub_pause_note("stays", view.get("runner_id")))


def _hub_pause_note(stance: str, runner_id: str | None) -> str:
    """The note that the hub's brake is on too, naming the verb that clears it by the runner's id — which
    the runner knows only from its first registration on."""
    if runner_id is None:
        return (
            f"note: it {stance} paused at the hub — this runner has not registered yet, so find its id in"
            " `blizzard hub runner list` to resume it there"
        )
    return f"note: it {stance} paused at the hub — clear that with `blizzard hub runner resume {runner_id}`"


@dto
@dataclass(frozen=True)
class SessionLabel:
    """A parked session's identity as a trailing clause — ``"  session=code (opus, high)"``.

    Empty when the escalation carries none of the three, so a bare line reads as
    "not recorded" rather than inventing one."""

    escalation: dict

    @property
    def text(self) -> str:  # ast-grep-ignore: bzh:property-delegates
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
    """The machine-local view: capacities, held environments, open asks, escalations, open takeovers.
    Every section is this runner's own local read, so the view renders fully with the
    hub unreachable; hub reachability is itself reported, not assumed."""
    with RunnerDaemon.reach("status", directory, runner_url) as daemon:
        view = daemon.get("/api/runner").json()
        leases_resp = daemon.get("/api/leases")
        envs_resp = daemon.get("/api/environments")
        asks_resp = daemon.get("/api/asks", params={"open": "true"})
        escalations_resp = daemon.get("/api/escalations")
        takeovers_resp = daemon.get("/api/takeovers")
        subscriptions_resp = daemon.get("/api/subscriptions")
        escalations = escalations_resp.json().get("items", [])
        harness_health = (
            daemon.get("/api/harness-health").json().get("items", [])
            if any(esc.get("cause") == EscalationCause.NO_ACCEPTABLE_HARNESS for esc in escalations)
            else []
        )
        # Not raised on: a runner that cannot serve its trace status still renders every other section.
        traces_resp = daemon.send("get", "/api/traces/status")

    click.echo(
        f"runner {view['runner_name']} ({view.get('runner_id') or 'not registered'})  workspace={view['workspace_id']}"
    )
    pause = view["pause"]
    brakes = [name for name, on in (("local", pause["local"]), ("hub", pause["hub"])) if on]
    brake_state = f"paused [{'+'.join(brakes)}]" if pause["effective"] else "running"
    reason = f" — {pause['local_reason']}" if pause.get("local_reason") else ""
    click.echo(f"  {brake_state}{reason}")
    cap = view["capacities"]
    click.echo(f"  capacity: {cap['used']}/{cap['max_agents']} used, {cap['free']} free")
    hub = view["hub"]
    reachability = "reachable" if hub["reachable"] else "unreachable"
    contact = hub["last_contact_at"] or "never"
    click.echo(f"  hub: {reachability} (last contact {contact}), {hub['buffer_depth']} fact(s) buffered")
    click.echo(f"  last tick: {view['last_tick_at'] or 'never'}")
    click.echo(f"  gates: {', '.join(view['gates']) or 'none'}")

    leases = [lease for lease in leases_resp.json().get("items", []) if lease.get("state") != "closed"]
    click.echo(f"\nleases ({len(leases)}):")
    for lease in leases:
        click.echo(f"  {lease['lease_id']}  {lease['state']:<12} chunk={lease['chunk_id']} node={lease['node_name']}")

    # `GET /api/environments` carries the full configured pool; this section
    # is the *held*-environments view, so it keeps the slots the runner reports held.
    envs = [env for env in envs_resp.json().get("items", []) if env.get("held")]
    click.echo(f"\nheld environments ({len(envs)}):")
    for env in envs:
        click.echo(f"  {env['environment_id']}  chunk={env['chunk_id']}  held since {env['held_since']}")

    asks = asks_resp.json().get("items", [])
    click.echo(f"\nopen asks ({len(asks)}):")
    for ask in asks:
        opts = f"  [{'|'.join(ask.get('options') or [])}]" if ask.get("options") else ""
        click.echo(f"  {ask['question_id']}  (chunk {ask['chunk_id']}): {ask['question']}{opts}")

    click.echo(f"\nescalations ({len(escalations)}):")
    for esc in escalations:
        click.echo(
            f"  chunk {esc['chunk_id']}  node={esc['node_id']}  since {esc['closed_at']}{SessionLabel(esc).text}"
        )
        if esc.get("wrapped_takeover_command"):
            click.echo(f"    takeover: {esc['wrapped_takeover_command']}")
        click.echo(f"    resume: {esc['resume_command']}")
        if esc.get("cause"):
            click.echo(f"    cause: {esc['cause']}")
        if esc.get("cause") == EscalationCause.NO_ACCEPTABLE_HARNESS:
            for health in harness_health:
                availability = "available" if health["available"] else "unavailable"
                click.echo(f"    harness {health['harness_id']}: {availability}, cause={health['cause'] or 'none'}")

    takeovers = takeovers_resp.json().get("items", [])
    click.echo(f"\nopen takeovers ({len(takeovers)}):")
    for tko in takeovers:
        click.echo(f"  chunk {tko['chunk_id']}  takeover={tko['takeover_id']}  held since {tko['held_since']}")

    subscriptions = subscriptions_resp.json().get("items", [])
    click.echo(f"\nsubscriptions ({len(subscriptions)}):")
    for sub in subscriptions:
        if sub["sampled_at"] is None:
            click.echo(f"  {sub['slug']} ({sub['provider']}): never sampled")
        elif sub["ok"]:
            click.echo(f"  {sub['slug']} ({sub['provider']}): ok, sampled at {sub['sampled_at']}")
        else:
            reason = sub["miss_reason"]
            miss_text = MISS_REASON_TEXT.get(SampleMissReason(reason), reason) if reason in SampleMissReason else reason
            click.echo(f"  {sub['slug']} ({sub['provider']}): miss ({miss_text}), last attempt {sub['sampled_at']}")
        renewal = renewal_text(sub)
        if renewal is not None:
            click.echo(f"    renewal: {renewal}")

    harness = traces_resp.json().get("harness_telemetry") if traces_resp.is_success else None
    harness_lines = harness_telemetry_lines(harness)
    if harness_lines:
        click.echo()
        for line in harness_lines:
            click.echo(line)


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
    """Declarative control: pause this runner — it starts no new workers. This runner's
    **own** brake, a pure client of its local API, so it works with the hub unreachable: a stalled
    worker is not killed, and an exhausted retry budget does not escalate, until it is cleared. No
    retry is consumed, and a live worker is left alone — this is not a drain. Distinct from the hub's
    brake, and each is cleared where it was set."""
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
    """Declarative control: clear this runner's own pause brake — it resumes spawning.

    The counterpart to ``blizzard runner pause``, and local in the same way. It clears only
    the local brake: a runner also paused at the hub stays paused until ``blizzard hub
    runner resume <runner_id>`` clears that one too."""
    _set_local_paused(paused=False, by=by, directory=directory, runner_url=runner_url)


@click.command()
@click.argument("chunk_id")
@click.option("--force", is_flag=True, default=False, help="Supersede a live worker attempt instead of refusing.")
@click.option(
    "--end",
    "end",
    is_flag=True,
    default=False,
    help="End the chunk's open takeover instead of starting a session — the recovery for a stranded one. "
    "The loop may then touch the chunk's session again, and `runner requeue` is no longer refused for a takeover. "
    "Reports nothing open, exiting 0, when none is.",
)
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
def takeover(chunk_id: str, force: bool, end: bool, directory: str, runner_url: str | None) -> None:
    """Take over a parked chunk: exec the interactive resume command in this terminal. The
    takeover fact is recorded before anything else runs, so no loop step can respawn or judge the
    session while it is open; the lease token travels only in the response body and the exec, never
    printed. ``--force`` supersedes a live worker attempt instead of refusing. An interrupted session
    still closes the takeover."""
    if end and force:
        raise click.UsageError("--end and --force are mutually exclusive: --end starts no session")
    if end:
        _end_open_takeover(chunk_id, directory, runner_url)
        return
    with RunnerDaemon.reach("takeover", directory, runner_url) as daemon:
        resp = daemon.send("post", f"/api/chunks/{chunk_id}/takeovers", json_body={"force": force})
        if resp.status_code == 409:
            raise click.ClickException(f"takeover: {resp.json().get('detail', 'chunk is not takeable')}")
        resp.raise_for_status()
        view = resp.json()
        click.echo(f"taking over chunk {chunk_id} in {view['workdir']}: {view['command']}")
        try:
            # The takeover env, layered over the terminal env: the forwarded
            # vars deliberately WIN over the terminal's own, and carry the lease token.
            child_env = {**os.environ, **view.get("env", {})}
            exit_code = subprocess.call(view["command"], shell=True, cwd=view["workdir"], env=child_env)
        finally:
            daemon.patch(f"/api/chunks/{chunk_id}/takeovers/{view['takeover_id']}")
    if exit_code != 0:
        raise SystemExit(exit_code)


def _end_open_takeover(chunk_id: str, directory: str, runner_url: str | None) -> None:
    """Close the chunk's open takeover through the runner's own API; idempotent when none is open."""
    with RunnerDaemon.reach("takeover", directory, runner_url) as daemon:
        open_for_chunk = [t for t in daemon.get("/api/takeovers").json().get("items", []) if t["chunk_id"] == chunk_id]
        if not open_for_chunk:
            click.echo(f"no open takeover for chunk {chunk_id}")
            return
        takeover_id = open_for_chunk[0]["takeover_id"]
        resp = daemon.send("patch", f"/api/chunks/{chunk_id}/takeovers/{takeover_id}")
        if resp.status_code == 404:
            click.echo(f"takeover {takeover_id} on chunk {chunk_id} was already ended elsewhere")
            return
        resp.raise_for_status()
    click.echo(f"ended takeover {takeover_id} on chunk {chunk_id}")


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
    """Hand a needs_human chunk back to the fleet: a fresh attempt at its current node.
    Clears the chunk's local needs_human hold; a fresh attempt spawns at the current node on the
    fleet's next pass. The route is never released and the chunk never re-enters the hub's queue.
    Refused ``409`` while its takeover is still open, or while it is not parked needs_human."""
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
    """Adapter-drift canary before an unattended period: exercises CODING_HARNESS against a
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


def renewal_text(sub: dict[str, object]) -> str | None:
    """One subscription's newest credential renewal in operator words, from the wire's typed
    fields alone — ``None`` when it was never renewed."""
    result = RenewalResult.recognized(sub.get("renewal_result"))
    if result is None:
        return None
    attempted_at = sub.get("renewal_attempted_at")
    if result is RenewalResult.RENEWED:
        return f"renewed at {attempted_at}"
    if result is RenewalResult.FAILED:
        reason = RenewalFailureReason.recognized(sub.get("renewal_failure_reason"))
        cause = RENEWAL_FAILURE_TEXT[reason] if reason is not None else "unknown cause"
        return f"failed ({cause}) at {attempted_at}"
    return f"attempted at {attempted_at}, outcome not recorded"
