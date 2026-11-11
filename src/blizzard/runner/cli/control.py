"""Operator-facing verbs that reach the local ``RunnerDaemon``: status, the pause brake, the floor clear, takeover,
requeue, selftest."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import click
from pydantic import TypeAdapter

from blizzard.cli.collaborators import CliCollaborators
from blizzard.cli.refusal import refusal_of
from blizzard.foundation.credential_renewal import RenewalFailureReason, RenewalResult
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.leases import LeaseLiveness
from blizzard.foundation.roles import dto
from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.foundation.subscription_miss import SampleMissReason
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.runner.cli.children import command_children
from blizzard.runner.cli.daemon import Door, RunnerDaemon, runner_option
from blizzard.runner.cli.env import DEFAULT_DIR, ENV_RUNNER_DIR
from blizzard.runner.cli.response import decode_answer
from blizzard.runner.cli.traces import harness_telemetry_lines
from blizzard.runner.hub.identity import IdentityVerdict
from blizzard.runner.process.child_env import ChildEnv
from blizzard.runner.process.child_processes import ShellCommand
from blizzard.runner.subscriptions.credential_renewer import RENEWAL_FAILURE_TEXT
from blizzard.runner.subscriptions.subscription_sampler import MISS_REASON_TEXT
from blizzard.wire.decision import OpenDecisionsResponse
from blizzard.wire.lease import LeaseListResponse
from blizzard.wire.runner_control import RunnerControlView
from blizzard.wire.runner_host import HostEnvironmentListResponse, HostSubscriptionListResponse
from blizzard.wire.runner_selftest import SelfTestView
from blizzard.wire.runner_status import (
    AskListResponse,
    EscalationListResponse,
    HarnessHealthListResponse,
    IdentityVerdictView,
    OpenTakeoverListResponse,
    RunnerStatusView,
)
from blizzard.wire.runner_traces import RunnerTraceStatusResponse
from blizzard.wire.takeover import TakeoverOpenResponse

# The operator's TCP address for the local API, overriding the socket.
ENV_LOCAL_API_URL = "BZ_RUNNER_URL"
# Each `selftest` poll is a machine-local read of already-computed state, so a short interval is free.
_SELFTEST_POLL_INTERVAL = 0.2
# A CLI-side backstop above the server's own authoritative run budget, so the CLI never spins forever
# against a runner that cannot reach that code.
_SELFTEST_POLL_TIMEOUT = 600.0


def _set_local_paused(
    *, paused: bool, by: str, directory: str, runner_url: str | None, runner: str | None, door: Door
) -> None:
    """PATCH the runner singleton's own pause brake — the declarative pattern applied locally."""
    with RunnerDaemon.reach("pause" if paused else "start", directory, runner_url, door, runner) as daemon:
        _, answer = decode_answer(
            daemon.patch("/api/runner", json_body={"paused": paused, "by": by}),
            TypeAdapter(RunnerControlView),
            verb="pause" if paused else "start",
            where="/api/runner",
        )
        view = answer.model_dump(mode="json")
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


def gate_decision_lines(decisions: list[dict[str, Any]] | None) -> list[str]:
    """The ``gate decisions`` section: each open decision this runner's gates imposed, or a single ``hub unreachable``
    line when the hub could not answer (``None``)."""
    if decisions is None:
        return ["", "gate decisions: hub unreachable"]
    return [
        "",
        f"gate decisions ({len(decisions)}):",
        *(f"  chunk={d['chunk_id']}  node={d['node_name']}  parked at {d['submitted_at']}" for d in decisions),
    ]


def _gates_line(gates: list[str], unenforceable: list[dict[str, Any]] | None) -> str:
    """The configured gates, each the hub flagged annotated with why it can never hold a chunk; plain until a hub that
    reports flags has answered."""
    reasons = {flag["node"]: flag["reason"] for flag in unenforceable or []}
    return ", ".join(f"{g} (not enforceable: {reasons[g]})" if g in reasons else g for g in gates) or "none"


def _served_line(served: list[dict[str, Any]] | None) -> str | None:
    """The status's ``serves:`` line — each project the hub resolved, by slug, then each entry no project
    answers to and each configured entry not answered yet, marked so — or ``None`` when nothing names any."""
    if served is None:
        return None
    resolved = [p.get("slug") or p["entry"] for p in served if p["resolved"]]
    unresolved = [f'"{p["entry"]}"' for p in served if p.get("answered", True) and not p["resolved"]]
    pending = [f'"{p["entry"]}"' for p in served if not p.get("answered", True)]
    parts = [", ".join(resolved)] if resolved else ([] if pending else ["nothing"])
    if unresolved:
        parts.append(f"unresolved: {', '.join(unresolved)}")
    if pending:
        parts.append(f"not yet answered: {', '.join(pending)}")
    return f"  serves: {'; '.join(parts)}"


def _tenant_part(view: dict[str, Any]) -> str:
    """The tenant clause of the status headline — ``"  tenant=acme (tn_1)"`` — or empty until a hub that
    names the runner's tenant has answered."""
    tenant_id = view.get("tenant_id")
    if tenant_id is None:
        return ""
    return f"  tenant={view.get('tenant_name') or tenant_id} ({tenant_id})"


def throttle_lines(throttle: dict[str, Any] | None) -> list[str]:
    if throttle is None:
        return []
    declining = ", ".join(throttle.get("declining", [])) or "none"
    floor = throttle["floor"]
    target = floor.get("subscription") or "runner"
    hold = floor.get("hold")
    answer = "admit" if floor["admits"] else "hold"
    if hold is not None:
        until = hold.get("grace_opens_at") or hold["expected_reset"]
        detail = f"{hold['kind']} ({hold.get('window') or hold.get('harness_id') or 'usage limit'}), until {until}"
        if hold["awaits_sample"]:
            detail += f"; reset {hold['expected_reset']}, awaiting sample"
    elif (clear := floor.get("clear")) is not None:
        detail = f"hand clear by {clear['by']} at {clear['cleared_at']}"
    else:
        detail = "open"
    lines = [f"  throttle: {throttle['state']} (declining: {declining})", f"    floor ({target}): {answer} — {detail}"]
    for lever in throttle.get("levers", []):
        lines.append(
            f"    {lever['name']} ({lever['setting']}): {'admit' if lever['admits'] else 'hold'}"
            f" — {lever['reading']}; until {lever['until'] or 'none'}"
        )
    lines.append(f"    on_decline: {throttle['on_decline']}; on_usage_limit: {throttle['on_usage_limit']}")
    return lines


@dto
@dataclass(frozen=True)
class SessionLabel:
    """A parked session's identity as a trailing clause — ``"  session=code (opus, high)"``.

    Empty when the escalation carries none of the three, so a bare line reads as
    "not recorded" rather than inventing one."""

    escalation: dict[str, Any]

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
@runner_option
@click.pass_context
def status(ctx: click.Context, directory: str, runner_url: str | None, runner: str | None) -> None:
    """The machine-local view: capacities, held environments, open asks, escalations, open takeovers.
    Every section is this runner's own local read, so the view renders fully with the
    hub unreachable; hub reachability is itself reported, not assumed."""
    with RunnerDaemon.reach("status", directory, runner_url, Door.of(ctx), runner) as daemon:
        runner_id = daemon.runner_id()
        view_resp = daemon.get("/api/runner")
        leases_resp = daemon.get("/api/leases")
        envs_resp = daemon.get_from_host("/api/host/environments")
        asks_resp = daemon.get("/api/asks", params={"open": "true"})
        escalations_resp = daemon.get("/api/escalations")
        takeovers_resp = daemon.get("/api/takeovers")
        subscriptions_resp = daemon.get_from_host("/api/host/subscriptions")
        _, escalations_answer = decode_answer(
            escalations_resp, TypeAdapter(EscalationListResponse), verb="status", where="/api/escalations"
        )
        escalations = escalations_answer.model_dump(mode="json")["items"]
        harness_health: list[dict[str, Any]] = []
        if any(esc.get("cause") == EscalationCause.NO_ACCEPTABLE_HARNESS for esc in escalations):
            _, health_answer = decode_answer(
                daemon.get("/api/harness-health"),
                TypeAdapter(HarnessHealthListResponse),
                verb="status",
                where="/api/harness-health",
            )
            harness_health = health_answer.model_dump(mode="json")["items"]
        # Not raised on: a runner that cannot serve its trace status still renders every other section.
        traces_resp = daemon.send("get", "/api/traces/status")
        # Not raised on: the decisions live on the hub, so a hub that cannot answer costs only this section.
        decisions_resp = daemon.send("get", "/api/gate-decisions")

    _, typed_view = decode_answer(view_resp, TypeAdapter(RunnerStatusView), verb="status", where="/api/runner")
    _, typed_leases = decode_answer(leases_resp, TypeAdapter(LeaseListResponse), verb="status", where="/api/leases")
    _, envs_answer = decode_answer(
        envs_resp, TypeAdapter(HostEnvironmentListResponse), verb="status", where="/api/host/environments"
    )
    _, asks_answer = decode_answer(asks_resp, TypeAdapter(AskListResponse), verb="status", where="/api/asks")
    _, takeovers_answer = decode_answer(
        takeovers_resp, TypeAdapter(OpenTakeoverListResponse), verb="status", where="/api/takeovers"
    )
    _, subscriptions_answer = decode_answer(
        subscriptions_resp, TypeAdapter(HostSubscriptionListResponse), verb="status", where="/api/host/subscriptions"
    )
    traces = (
        decode_answer(traces_resp, TypeAdapter(RunnerTraceStatusResponse), verb="status", where="/api/traces/status")[1]
        if traces_resp.is_success
        else None
    )
    decisions_data = (
        decode_answer(decisions_resp, TypeAdapter(OpenDecisionsResponse), verb="status", where="/api/gate-decisions")[1]
        if decisions_resp.is_success
        else None
    )

    view = typed_view.model_dump(mode="json")
    leases_data = typed_leases.model_dump(mode="json")
    envs_data = envs_answer.model_dump(mode="json")
    asks_data = asks_answer.model_dump(mode="json")
    takeovers_data = takeovers_answer.model_dump(mode="json")
    subscriptions_data = subscriptions_answer.model_dump(mode="json")
    click.echo(
        f"runner {view['runner_name']} ({view.get('runner_id') or 'not registered'}){_tenant_part(view)}"
        f"  workspace={view['workspace_id']}"
    )
    identity = typed_view.identity
    if identity is not None:
        echo_identity(identity)
    pause = view["pause"]
    brakes = [name for name, on in (("local", pause["local"]), ("hub", pause["hub"])) if on]
    brake_state = f"paused [{'+'.join(brakes)}]" if pause["effective"] else "running"
    reason = f" — {pause.get('local_reason')}" if pause.get("local_reason") else ""
    click.echo(f"  {brake_state}{reason}")
    for line in throttle_lines(view.get("throttle")):
        click.echo(line)
    cap = view["capacities"]
    bindings = cap.get("bindings", [])
    click.echo(f"  capacity: {cap['used']}/{cap['agents']} used")
    for b in bindings:
        resolved = "" if b.get("project_id") else " (unresolved)"
        click.echo(f"    {b['project']}{resolved} -> {b['workspace_id']}: {b['used']} working")
    for conflict in view.get("routing_conflicts", []):
        click.echo(
            f"  NOT CLAIMING: {', '.join(conflict['entries'])} all resolve to project {conflict['project_id']}"
            " — bind each project once"
        )
    hub = view["hub"]
    reachability = "reachable" if hub["reachable"] else "unreachable"
    contact = hub["last_contact_at"] or "never"
    click.echo(f"  hub: {reachability} (last contact {contact}), {hub['buffer_depth']} fact(s) buffered")
    click.echo(f"  last tick: {view['last_tick_at'] or 'never'}")
    click.echo(f"  gates: {_gates_line(view['gates'], view.get('unenforceable_gates'))}")
    served = _served_line(view.get("served_projects"))
    if served is not None:
        click.echo(served)

    leases = [
        raw
        for raw, typed in zip(leases_data.get("items", []), typed_leases.items, strict=True)
        if typed.liveness != LeaseLiveness.ENDED
    ]
    click.echo(f"\nleases ({len(leases)}):")
    for lease in leases:
        click.echo(f"  {lease['lease_id']}  {lease['state']:<12} chunk={lease['chunk_id']} node={lease['node_name']}")

    # `GET /api/host/environments` carries every workspace's whole pool; this section
    # is this runner's *held*-environments view, so it keeps the slots this runner holds.
    envs = [
        env
        for workspace in envs_data.get("workspaces", [])
        for env in workspace.get("items", [])
        if env.get("held") and env.get("runner_id") == runner_id
    ]
    click.echo(f"\nheld environments ({len(envs)}):")
    for env in envs:
        click.echo(f"  {env['environment_id']}  chunk={env['chunk_id']}  held since {env['held_since']}")
    # Absent from a runner that predates the field; each one still holds its lease and agent slot.
    unmatched: list[dict[str, Any]] = view.get("unmatched_bindings") or []
    if unmatched:
        click.echo(f"\nheld bindings matching no workspace ({len(unmatched)}):")
        for binding in unmatched:
            click.echo(
                f"  {binding['environment_id']}  chunk={binding['chunk_id']}  workdir={binding['workdir']}"
                f"  held since {binding['held_since']}"
            )

    asks = asks_data.get("items", [])
    click.echo(f"\nopen asks ({len(asks)}):")
    for ask in asks:
        opts = f"  [{'|'.join(ask.get('options') or [])}]" if ask.get("options") else ""
        click.echo(f"  {ask['question_id']}  (chunk {ask['chunk_id']}): {ask['question']}{opts}")

    for line in gate_decision_lines(decisions_data.model_dump(mode="json")["decisions"] if decisions_data else None):
        click.echo(line)

    click.echo(f"\nescalations ({len(escalations)}):")
    for esc in escalations:
        click.echo(
            f"  chunk {esc['chunk_id']}  node={esc['node_id']}  since {esc['closed_at']}{SessionLabel(esc).text}"
        )
        if esc.get("wrapped_takeover_command"):
            click.echo(f"    takeover: {esc.get('wrapped_takeover_command')}")
        click.echo(f"    resume: {esc['resume_command']}")
        if esc.get("cause"):
            click.echo(f"    cause: {esc.get('cause')}")
        if esc.get("cause") == EscalationCause.NO_ACCEPTABLE_HARNESS:
            for health in harness_health:
                availability = "available" if health["available"] else "unavailable"
                click.echo(f"    harness {health['harness_id']}: {availability}, cause={health['cause'] or 'none'}")

    takeovers = takeovers_data.get("items", [])
    click.echo(f"\nopen takeovers ({len(takeovers)}):")
    for tko in takeovers:
        click.echo(f"  chunk {tko['chunk_id']}  takeover={tko['takeover_id']}  held since {tko['held_since']}")

    subscriptions = [sub for sub in subscriptions_data.get("items", []) if runner_id in sub.get("spenders", [])]
    click.echo(f"\nsubscriptions ({len(subscriptions)}):")
    for sub in subscriptions:
        if sub["sampled_at"] is None:
            click.echo(f"  {sub['slug']} ({sub['provider']}): never sampled")
        elif sub.get("ok") and sub.get("condition") is None:
            click.echo(f"  {sub['slug']} ({sub['provider']}): ok, sampled at {sub['sampled_at']}")
        else:
            reason = sub.get("miss_reason") or sub.get("condition") or "unknown"
            miss_text = MISS_REASON_TEXT.get(SampleMissReason(reason), reason) if reason in SampleMissReason else reason
            click.echo(f"  {sub['slug']} ({sub['provider']}): miss ({miss_text}), last attempt {sub['sampled_at']}")
        renewal = renewal_text(sub)
        if renewal is not None:
            click.echo(f"    renewal: {renewal}")

    harness = traces.harness_telemetry if traces is not None else None
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
@runner_option
@click.option("--by", "by", default="operator", help="Who is pausing, recorded as the actor.")
@click.pass_context
def pause(ctx: click.Context, directory: str, runner_url: str | None, runner: str | None, by: str) -> None:
    """Pause this runner: it starts no new workers until ``blizzard runner start``, and works with the hub
    unreachable. Running workers are left alone — this is not a drain — but while paused a stalled worker is not
    killed and an exhausted retry budget does not escalate. Separate from a pause set at the hub; each is cleared
    where it was set."""
    _set_local_paused(paused=True, by=by, directory=directory, runner_url=runner_url, runner=runner, door=Door.of(ctx))


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
@runner_option
@click.option("--by", "by", default="operator", help="Who is starting, recorded as the actor.")
@click.pass_context
def start(ctx: click.Context, directory: str, runner_url: str | None, runner: str | None, by: str) -> None:
    """Clear the pause ``blizzard runner pause`` set: this runner starts new workers again.

    Works with the hub unreachable. A runner also paused at the hub stays paused until
    ``blizzard hub runner resume <runner_id>`` clears that one too."""
    _set_local_paused(paused=False, by=by, directory=directory, runner_url=runner_url, runner=runner, door=Door.of(ctx))


@click.command("clear-floor")
@click.option(
    "--subscription",
    "subscription",
    default=None,
    help="The declared subscription whose usage-limit floor to open, for every runner spending it.",
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
@runner_option
@click.option("--by", "by", default="operator", help="Who is clearing, recorded as the actor.")
@click.pass_context
def clear_floor(
    ctx: click.Context, subscription: str | None, directory: str, runner_url: str | None, runner: str | None, by: str
) -> None:
    """Open a usage-limit floor now, before its reset: a subscription's with ``--subscription``, for every runner
    spending it, or with ``--runner`` the floor of a runner spending none. Exactly one of the two.

    A runner that spends a subscription refuses ``--runner``, naming the subscription to clear instead."""
    if (subscription is None) == (runner is None):
        raise click.UsageError("exactly one of --subscription and --runner is required")
    with RunnerDaemon.reach("clear-floor", directory, runner_url, Door.of(ctx), runner) as daemon:
        if subscription is not None:
            resp = daemon.send_to_host(
                "post", f"/api/host/subscriptions/{subscription}/floor/clear", json_body={"by": by}
            )
            if resp.status_code == 404:
                raise click.ClickException(f"clear-floor: the host declares no subscription {subscription!r}")
        else:
            resp = daemon.send("post", "/api/runner/floor/clear", json_body={"by": by})
            if resp.status_code == 409:
                held = refusal_of(resp).detail
                slug = cast(dict[str, object], held).get("slug") if isinstance(held, dict) else None
                raise click.ClickException(
                    f"clear-floor: runner {runner} spends subscription {slug!r} — its floor is that subscription's;"
                    f" clear it with `blizzard runner clear-floor --subscription {slug}`"
                )
        resp.raise_for_status()
    target = f"subscription {subscription}" if subscription is not None else f"runner {runner}"
    click.echo(f"cleared the usage-limit floor of {target} — held starts go ahead on their next ask")


@click.command()
@click.argument("chunk_id")
@click.option("--force", is_flag=True, default=False, help="End a running worker instead of refusing.")
@click.option(
    "--end",
    "end",
    is_flag=True,
    default=False,
    help="End the chunk's open takeover instead of starting a session — the recovery for a stranded one. "
    "The runner resumes driving the chunk's session, and `runner requeue` is no longer refused for a takeover. "
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
@runner_option
@click.pass_context
def takeover(
    ctx: click.Context,
    chunk_id: str,
    force: bool,
    end: bool,
    directory: str,
    runner_url: str | None,
    runner: str | None,
) -> None:
    """Take over a chunk this runner holds: resume its session interactively in this terminal. The runner
    leaves the chunk alone until the session exits, and an interrupted session still ends the takeover.
    Refused when this runner holds no environment for the chunk, a takeover is already open, there is no
    session to resume, a worker is running (unless ``--force``), or its result is still waiting to reach
    the hub."""
    if end and force:
        raise click.UsageError("--end and --force are mutually exclusive: --end starts no session")
    if end:
        _end_open_takeover(chunk_id, directory, runner_url, runner, Door.of(ctx))
        return
    with RunnerDaemon.reach("takeover", directory, runner_url, Door.of(ctx), runner) as daemon:
        resp = daemon.send("post", f"/api/chunks/{chunk_id}/takeovers", json_body={"force": force})
        if resp.status_code == 409:
            raise click.ClickException(f"takeover: {resp.json().get('detail', 'chunk is not takeable')}")
        resp.raise_for_status()
        _, answer = decode_answer(
            resp, TypeAdapter(TakeoverOpenResponse), verb="takeover", where=f"/api/chunks/{chunk_id}/takeovers"
        )
        view = answer.model_dump(mode="json")
        click.echo(f"taking over chunk {chunk_id} in {view['workdir']}: {view['command']}")
        try:
            exit_code = command_children(ctx).run_foreground(
                ShellCommand(view["command"]),
                cwd=view["workdir"],
                env=ChildEnv.operator_shell(view.get("env", {})),
            )
        finally:
            daemon.patch(f"/api/chunks/{chunk_id}/takeovers/{view['takeover_id']}")
    if exit_code != 0:
        raise SystemExit(exit_code)


def _end_open_takeover(chunk_id: str, directory: str, runner_url: str | None, runner: str | None, door: Door) -> None:
    """Close the chunk's open takeover through the runner's own API; idempotent when none is open."""
    with RunnerDaemon.reach("takeover", directory, runner_url, door, runner) as daemon:
        _, answer = decode_answer(
            daemon.get("/api/takeovers"), TypeAdapter(OpenTakeoverListResponse), verb="takeover", where="/api/takeovers"
        )
        data = answer.model_dump(mode="json")
        open_for_chunk = [t for t in data.get("items", []) if t["chunk_id"] == chunk_id]
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
@runner_option
@click.pass_context
def requeue(ctx: click.Context, chunk_id: str, directory: str, runner_url: str | None, runner: str | None) -> None:
    """Hand a needs_human chunk back to this runner after a human has worked it: one more try at its current
    node, with its retry budget carried over. The runner keeps its claim, so the chunk never returns to the
    hub's queue. Refused while a takeover of the chunk is open, when the chunk is not needs_human, or when
    this runner holds no environment for it; repeating a pending requeue changes nothing."""
    with RunnerDaemon.reach("requeue", directory, runner_url, Door.of(ctx), runner) as daemon:
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
@runner_option
@click.pass_context
def selftest(
    ctx: click.Context, coding_harness: str, directory: str, runner_url: str | None, runner: str | None
) -> None:
    """Check CODING_HARNESS still works before an unattended period: runs it against a throwaway scratch repo
    — a spawn, a trivial edit and commit, a verdict, an automated resume, and the resume command — touching
    no chunk, environment, or hub. Prints each check and exits non-zero when any fails; refused for an
    unknown coding harness."""
    with RunnerDaemon.reach("selftest", directory, runner_url, Door.of(ctx), runner) as daemon:
        resp = daemon.send("post", "/api/selftests", json_body={"harness": coding_harness})
        if resp.status_code == 422:
            raise click.ClickException(resp.json().get("detail", "unknown coding harness"))
        resp.raise_for_status()
        _, answer = decode_answer(resp, TypeAdapter(SelfTestView), verb="selftest", where="/api/selftests")
        run = answer.model_dump(mode="json")
        monotonic_clock = CliCollaborators.of(ctx).monotonic_clock
        deadline = monotonic_clock.monotonic() + _SELFTEST_POLL_TIMEOUT
        while run["status"] == "running":
            if monotonic_clock.monotonic() > deadline:
                raise click.ClickException(
                    f"selftest {run['id']} did not finish within {_SELFTEST_POLL_TIMEOUT:g}s — the runner may be wedged"
                )
            monotonic_clock.sleep(_SELFTEST_POLL_INTERVAL)
            path = f"/api/selftests/{run['id']}"
            _, answer = decode_answer(daemon.get(path), TypeAdapter(SelfTestView), verb="selftest", where=path)
            run = answer.model_dump(mode="json")

    for check in run["checks"]:
        mark = "PASS" if check["passed"] else "FAIL"
        click.echo(f"[{mark}] {check['name']}: {check['detail']}")
    if run["status"] != "passed":
        if run.get("error"):
            click.echo(f"selftest error: {run['error']}", err=True)
        click.echo(f"selftest {run['id']} FAILED for {coding_harness}", err=True)
        raise click.exceptions.Exit(1)
    click.echo(f"selftest {run['id']} passed for {coding_harness}")


def renewal_text(sub: Mapping[str, object]) -> str | None:
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


def _verdict(view: IdentityVerdictView, state: IdentityState) -> IdentityVerdict:
    return IdentityVerdict(
        state=state,
        store_runner_id=view.store_runner_id,
        token_runner_id=view.token_runner_id,
        checked_at=datetime.fromisoformat(view.checked_at) if view.checked_at is not None else None,
        reset_command=view.reset_command,
        reason=view.reason,
    )


def echo_identity(view: IdentityVerdictView) -> None:
    if isinstance(view.state, Unrecognized):
        # A state a newer runner reports: show it, and claim no refusal or remedy for it.
        click.echo(f"  identity: unknown identity state {view.state.raw!r}")
        return
    verdict = _verdict(view, view.state)
    if verdict.state is IdentityState.UNCHECKED:
        click.echo("  identity: unchecked — waiting for the hub to answer which runner the token belongs to")
        return
    refusal = verdict.refusal()
    if refusal is None:
        return
    label = "HALTED" if verdict.halted_by_standing() else "REFUSING"
    remedy = verdict.remedy()
    click.echo(f"  {label}: {refusal}" + (f" — {remedy}" if remedy is not None else ""))
