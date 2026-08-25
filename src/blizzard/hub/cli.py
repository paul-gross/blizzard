"""``blizzard hub <cmd>`` — the fleet surface.

Client verbs are pure clients of the hub's HTTP API; ``host`` *becomes* the hub
daemon. This module is CLI top-level glue, so ``echo`` for user output is fine here
(``bzh:structlog-logging``). Operator verbs are grouped by noun (issue #104);
``status`` stays top-level as a cross-resource dashboard."""

from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click
import httpx
import uvicorn
import yaml

from blizzard.cli.host_directory import HostDirectory
from blizzard.foundation.events.server import EarlyShutdownServer as _EarlyShutdownServer
from blizzard.foundation.store.migrations import RevisionMismatchError
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub import cli_login, session_store
from blizzard.hub.api.marker_auth import _MARKER_TOKEN_HEADER
from blizzard.hub.app import build_hosted_app
from blizzard.hub.cli_context import CLIENT_TIMEOUT, DEFAULT_HUB_URL, ENV_HUB_URL, CliContext
from blizzard.hub.cli_views import (
    ChunkDetail,
    ChunkListing,
    ChunkSpendListing,
    CountsListing,
    DecisionListing,
    DurationsListing,
    EventListing,
    FleetStatus,
    GraphDetail,
    GraphListing,
    GraphSyncListing,
    Listing,
    MigrationIntent,
    OutcomesListing,
    QuestionListing,
    QueueListing,
    RunnerDetail,
    RunnerListing,
    SpendListing,
    WorkItemListing,
)
from blizzard.hub.config import ConfigError, HubConfig
from blizzard.hub.delivery.hub_node import ENV_MARKER_CALLBACK_URL, ENV_MARKER_TOKEN
from blizzard.hub.graphs import GraphFile
from blizzard.hub.runtime import ensure_current_revision, init_environment, migrate, migration_runner

# The runtime root the dir-taking verbs resolve, highest to lowest: explicit ``--dir``,
# then ``BZ_HUB_DIR``, then the cwd. Selectable, not shareable: the store is single-writer.
ENV_HUB_DIR = "BZ_HUB_DIR"
DEFAULT_DIR = "."


class HubCommand(click.Command):
    """An operator verb (issue #104): it declares the connection options; the callback takes their ``CliContext``."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.params = self.connected(self.params)

    @property
    def hub_url_option(self) -> click.Option:
        return click.Option(
            ["--hub-url", "hub_url"],
            default=None,
            help=f"Hub API base URL (default ${ENV_HUB_URL} or {DEFAULT_HUB_URL}).",
        )

    @property
    def json_option(self) -> click.Option:
        return click.Option(
            ["--json", "as_json"], is_flag=True, default=False, help="Print the raw response body as JSON."
        )

    def connected(self, params: list[click.Parameter]) -> list[click.Parameter]:
        """This verb's own parameters, with the connection options where the verb renders them."""
        raise NotImplementedError

    def context(self, params: dict[str, Any]) -> CliContext:
        """The context those options resolve to, consumed out of ``params``."""
        raise NotImplementedError

    def invoke(self, ctx: click.Context) -> Any:
        ctx.params["cli"] = self.context(ctx.params)
        return super().invoke(ctx)


class FleetCommand(HubCommand):
    """A verb that renders a hub response body, so ``--json`` prints it raw."""

    def connected(self, params: list[click.Parameter]) -> list[click.Parameter]:
        return [*params, self.json_option, self.hub_url_option]

    def context(self, params: dict[str, Any]) -> CliContext:
        return CliContext.of(params.pop("hub_url"), params.pop("as_json"))


class AuthCommand(HubCommand):
    """A verb over the hub's own ``/api/auth`` surface: it prints a status line, so no ``--json``."""

    def connected(self, params: list[click.Parameter]) -> list[click.Parameter]:
        return [self.hub_url_option, *params]

    def context(self, params: dict[str, Any]) -> CliContext:
        return CliContext.of(params.pop("hub_url"))


# The since-the-beginning-of-time cutoff `hub status` passes ``GET /api/spend`` (issue #60).
_FLEET_SPEND_SINCE = "1970-01-01T00:00:00+00:00"


@click.group(invoke_without_command=True)
@click.pass_context
def hub(ctx: click.Context) -> None:
    """Talk to — or become — the blizzard hub."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(host)


_ALLOW_EXTERNAL_DB_HELP = (
    "Proceed even if the config's db_url names a database outside this directory (issue #234's --dir isolation guard)."
)


@hub.command()
@click.argument("directory", default=DEFAULT_DIR, envvar=ENV_HUB_DIR)
@click.option("--allow-external-db", "allow_external_db", is_flag=True, default=False, help=_ALLOW_EXTERNAL_DB_HELP)
def init(directory: str, allow_external_db: bool) -> None:
    """Scaffold config + data dir + a migrated store under DIRECTORY. Idempotent.

    DIRECTORY defaults to $BZ_HUB_DIR, then the cwd."""
    try:
        config = init_environment(Path(directory), allow_external_db=allow_external_db)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    revision = migration_runner(config).current_revision()
    click.echo(f"hub runtime ready at {config.root} (store revision {revision})")


@hub.command("migrate")
@click.option(
    "--dir", "directory", default=DEFAULT_DIR, envvar=ENV_HUB_DIR, help="Hub runtime directory (overrides $BZ_HUB_DIR)."
)
@click.option("--down", default=None, help="Reverse migrations down to this revision (e.g. base).")
@click.option("--allow-external-db", "allow_external_db", is_flag=True, default=False, help=_ALLOW_EXTERNAL_DB_HELP)
def migrate_cmd(directory: str, down: str | None, allow_external_db: bool) -> None:
    """Apply pending store migrations, or reverse with --down <rev>."""
    try:
        migrate(Path(directory), down=down, allow_external_db=allow_external_db)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("migrated" if down is None else f"reversed to {down}")


# Bounds uvicorn's own connection-drain wait — defense-in-depth, not the fix for issue #47
# (see ``_EarlyShutdownServer``, the shared foundation wrapper, imported above).
_GRACEFUL_SHUTDOWN_SECONDS = 5


@hub.command()
@click.argument("directory", required=False, default=None)
@click.option(
    "--dir",
    "dir_option",
    default=DEFAULT_DIR,
    envvar=ENV_HUB_DIR,
    help="Hub runtime directory (overrides $BZ_HUB_DIR).",
)
@click.option("--host", "host_", default=None, help="Bind host (overrides config).")
@click.option("--port", type=int, default=None, help="Bind port (overrides config).")
@click.option("--allow-external-db", "allow_external_db", is_flag=True, default=False, help=_ALLOW_EXTERNAL_DB_HELP)
def host(directory: str | None, dir_option: str, host_: str | None, port: int | None, allow_external_db: bool) -> None:
    """Become the blizzard-hub daemon: HTTP API + SSE + the embedded web app.

    DIRECTORY (positional) and --dir are equivalent — pass one; giving both requires
    they agree. Defaults to $BZ_HUB_DIR, then the cwd."""
    directory = HostDirectory(directory, dir_option).path
    try:
        config = HubConfig.load(Path(directory), host=host_, port=port, allow_external_db=allow_external_db)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        ensure_current_revision(config)
    except RevisionMismatchError as exc:
        raise click.ClickException(str(exc)) from exc
    # Composition can still reject the config at boot; surface it as a clean CLI error,
    # and build before announcing so we never claim to serve and then die.
    try:
        app = build_hosted_app(config)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"serving blizzard-hub on {config.host}:{config.port}")
    uvicorn_config = uvicorn.Config(
        app, host=config.host, port=config.port, timeout_graceful_shutdown=_GRACEFUL_SHUTDOWN_SECONDS
    )
    _EarlyShutdownServer(uvicorn_config, shutdown_signal=app.state.shutdown).run()


@hub.command(cls=FleetCommand)
def status(cli: CliContext) -> None:
    """The fleet view: every chunk with its derived status, the runners, and open questions."""
    chunks = cli.get("/api/chunks", "GET /chunks")
    runners = cli.get("/api/runners", "GET /runners")
    questions = cli.get("/api/questions", "GET /questions")
    spend = cli.get("/api/spend", "GET /spend", params={"since": _FLEET_SPEND_SINCE})

    view = FleetStatus(
        chunks=chunks.json(),
        runners=runners.json().get("runners", []),
        questions=questions.json(),
        spend=spend.json(),
    )
    cli.show(
        {"chunks": view.chunks, "runners": runners.json(), "questions": view.questions, "spend": view.spend},
        view,
    )


class Pointer(click.ParamType):
    """One ``ingest`` argument as typed, bound to the token the hub is handed. The CLI carries no
    pointer grammar of its own, so a token travels through verbatim — bar a deprecated prefix."""

    name = "pointer"

    def convert(self, value: str, param: click.Parameter | None, ctx: click.Context | None) -> str:
        if not value.startswith("github:"):
            return value
        rest = value[len("github:") :]
        click.echo(
            f"warning: the 'github:' pointer prefix is deprecated (in {value!r}) — resolving {rest!r} on its own",
            err=True,
        )
        return rest


@hub.command("record-marker")
@click.argument("name")
@click.argument("content", required=False, default="")
def record_marker(name: str, content: str) -> None:
    """A hub command node's ``run:`` script: record a marker artifact mid-run (#65).

    The injected ``BZ_HUB_MARKER_CALLBACK_URL`` already carries this run's chunk, node,
    and epoch. Idempotent per marker NAME; authorized by ``BZ_HUB_MARKER_TOKEN``
    (issue #230), whose absence is named rather than posted unauthenticated."""
    callback_url = os.environ.get(ENV_MARKER_CALLBACK_URL)
    if not callback_url:
        raise click.ClickException(f"record-marker: no {ENV_MARKER_CALLBACK_URL} in the environment")
    marker_token = os.environ.get(ENV_MARKER_TOKEN)
    if not marker_token:
        raise click.ClickException(f"record-marker: no {ENV_MARKER_TOKEN} in the environment")
    try:
        resp = httpx.post(
            callback_url,
            json={"name": name, "content": content},
            headers={_MARKER_TOKEN_HEADER: marker_token},
            timeout=CLIENT_TIMEOUT,
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise click.ClickException(f"record-marker: could not record the marker ({exc})") from exc
    click.echo(f"recorded marker `{name}`")


@hub.command("rotate-signing-key", cls=AuthCommand)
def rotate_signing_key(cli: CliContext) -> None:
    """Rotate the hub's IdP signing keypair (issue #95) — mints a fresh current key,
    demoting the old current to previous; no restart. A no-op error under ``auth.mode = "none"``
    (no keypair exists). Human-plane, gated on ``user:manage`` — under ``auth.mode =
    "oauth"`` this requires a hub session (``blizzard hub login``, issue #96)."""
    cli.post(
        "/api/auth/rotate-signing-key",
        "POST /auth/rotate-signing-key",
        on_status={404: "the IdP surface is not enabled (auth.mode=none)"},
    )
    click.echo("signing key rotated")


@hub.command(cls=AuthCommand)
@click.option(
    "--paste",
    "paste",
    is_flag=True,
    default=False,
    help="Use the paste-code flow (no local loopback listener) instead of opening a browser.",
)
@click.option(
    "--no-browser", "no_browser", is_flag=True, default=False, help="Print the login URL instead of opening it."
)
def login(cli: CliContext, paste: bool, no_browser: bool) -> None:
    """Log into the hub (issue #96) — opens the browser to the hub's own authorize
    endpoint (PKCE, an ephemeral ``127.0.0.1`` loopback redirect) and stores the
    resulting session token locally. The CLI never contacts a provider directly.
    ``--paste`` uses the paste-code fallback for a shell with no reachable loopback
    listener; ``--no-browser`` still runs the loopback flow, printing the URL."""
    try:
        flow = (
            cli_login.Login.paste_code(cli.hub_url, prompt_for_code=lambda: click.prompt("Paste the code"))
            if paste
            else cli_login.Login.loopback(cli.hub_url, open_browser=not no_browser)
        )
        token = flow.token()
    except cli_login.LoginError as exc:
        raise click.ClickException(f"login failed: {exc}") from exc
    session_store.SessionFile.of().save(cli.hub_url, token)
    click.echo(f"logged in to {cli.hub_url}")


@hub.command(cls=AuthCommand)
def logout(cli: CliContext) -> None:
    """Log out of the hub (issue #96) — deletes the locally stored session token and
    revokes it at the hub, so it stops resolving even if it leaked. A no-op (locally)
    if never logged in; the revoke call is best-effort (a hub already unreachable, or
    an already-expired session, does not block the local cleanup)."""
    with contextlib.suppress(click.ClickException):
        cli.send("post", "/api/auth/logout")
    session_store.SessionFile.of().delete(cli.hub_url)
    click.echo(f"logged out of {cli.hub_url}")


# `blizzard hub chunk` — issue #104


@hub.group("chunk")
def chunk_group() -> None:
    """Operator verbs over one chunk: ingest, inspect, edit, and transition it."""


@chunk_group.command("list", cls=FleetCommand)
def chunk_list(cli: CliContext) -> None:
    """The fleet chunk list — derived status per chunk."""
    rows = cli.get("/api/chunks", "GET /chunks").json()
    cli.show(rows, ChunkListing(rows))


@chunk_group.command("show", cls=FleetCommand)
@click.argument("chunk_id")
def chunk_show(cli: CliContext, chunk_id: str) -> None:
    """One chunk's full aggregate — status, current node, route, pointers, cost."""
    resp = cli.get(f"/api/chunks/{chunk_id}", "GET /chunks/{id}", on_status={404: f"unknown chunk {chunk_id}"})
    detail = resp.json()
    cli.show(detail, ChunkDetail(detail))


@chunk_group.command("ingest", cls=FleetCommand)
@click.argument("pointers", nargs=-1, required=True, type=Pointer())
def chunk_ingest(cli: CliContext, pointers: tuple[str, ...]) -> None:
    """Ingest work items by token, minting a chunk.

    Each POINTER is a source-native token — ``source:ref``, ``source#ref``, or a pasted
    work item URL; a batch mints one chunk carrying every pointer. 422 when no
    configured work source claims a token; 409 when a pointer is already held."""
    tokens = list(pointers)
    resp = cli.send("post", "/api/chunks", json_body={"tokens": tokens})
    if resp.status_code == httpx.codes.CONFLICT:
        conflict = resp.json()
        raise click.ClickException(
            f"pointer {conflict.get('source')}#{conflict.get('ref')} already held by "
            f"chunk {conflict.get('existing_chunk_id')}"
        )
    cli.check(resp, "POST /chunks", on_status={422: "at least one token required"})
    body = resp.json()
    cli.show_lines(body, f"ingested {len(tokens)} pointer(s) → chunk {body['chunk_id']}")


@chunk_group.command("set", cls=FleetCommand)
@click.argument("chunk_id")
@click.option(
    "--graph",
    "graph_id",
    default=None,
    help="Repin CHUNK's workflow graph to this graph id. Legal only while CHUNK has never moved.",
)
@click.option(
    "--default-model",
    "default_model",
    multiple=True,
    help=(
        "Repin CHUNK's default model preference. Repeatable and ORDERED — the first entry "
        "that resolves at session mint wins. An entry is a `blizzard:` tier alias "
        "(frontier/advanced/basic) or a harness-native model name."
    ),
)
@click.option("--default-effort", "default_effort", default=None, help="Repin CHUNK's default effort.")
def chunk_set(
    cli: CliContext, chunk_id: str, graph_id: str | None, default_model: tuple[str, ...], default_effort: str | None
) -> None:
    """Repin CHUNK's graph and/or default model/effort in one call (issues #104, #144).

    A pure client of ``PATCH /api/chunks/{id}``, naming whichever fields were given and
    applied all-or-nothing. At least one option is required; 409 for the defaults once
    CHUNK is claimed, and for ``--graph`` once it is claimed or has moved (#271)."""
    if graph_id is None and not default_model and default_effort is None:
        raise click.UsageError("at least one of --graph/--default-model/--default-effort is required")
    body: dict[str, object] = {}
    if graph_id is not None:
        body["graph_id"] = graph_id
    if default_model:
        body["default_model"] = list(default_model)
    if default_effort is not None:
        body["default_effort"] = default_effort
    resp = cli.patch(
        f"/api/chunks/{chunk_id}",
        "PATCH /chunks/{id}",
        json_body=body,
        on_status={404: f"unknown chunk {chunk_id}", 409: "chunk is not editable", 422: "invalid request"},
    )
    view = resp.json()
    parts = []
    if graph_id is not None:
        parts.append(f"graph → {view['graph_id']}")
    if default_model:
        parts.append(f"default model → {', '.join(view.get('default_model') or []) or '-'}")
    if default_effort is not None:
        parts.append(f"default effort → {view.get('default_effort') or '-'}")
    cli.show_lines(view, f"{chunk_id}: {', '.join(parts)}")


@chunk_group.command("promote", cls=FleetCommand)
@click.argument("chunk_id")
def chunk_promote(cli: CliContext, chunk_id: str) -> None:
    """Promote a not-ready CHUNK to ready so a runner may claim it.

    A pure client of the hub API: ``POST /api/chunks/{id}/promote``. Idempotent — promoting
    an already-ready chunk is a harmless no-op; 404 only when the chunk is unknown."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/promote", "POST /chunks/{id}/promote", on_status={404: f"no such chunk {chunk_id}"}
    )
    cli.finish(resp, f"promoted {chunk_id} — now ready for a runner to claim")


@chunk_group.command("pause", cls=FleetCommand)
@click.argument("chunk_id")
@click.option("--by", "by", default="operator", help="Who is pausing (recorded on the fact).")
def chunk_pause(cli: CliContext, chunk_id: str, by: str) -> None:
    """Pause CHUNK — the runner kills and parks the worker but keeps the claim (issue #46).

    A pure client of the hub API: ``POST /api/chunks/{id}/pause``. Unlike ``detach``, no
    route is released and no retry is consumed. 409 when the chunk is done/stopped/
    delivering."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/pause",
        "POST /chunks/{id}/pause",
        json_body={"by": by},
        on_status={409: "chunk is not pausable", 404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"paused {chunk_id} — its worker will be killed and parked, keeping the claim")


@chunk_group.command("resume", cls=FleetCommand)
@click.argument("chunk_id")
@click.option("--by", "by", default="operator", help="Who is resuming (recorded on the fact).")
def chunk_resume(cli: CliContext, chunk_id: str, by: str) -> None:
    """Resume a paused CHUNK — the runner resumes the parked worker in place (issue #46).

    A pure client of the hub API: ``POST /api/chunks/{id}/resume``. Idempotent: resuming
    an unpaused chunk is a harmless no-op. 404 only when the chunk is unknown."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/resume",
        "POST /chunks/{id}/resume",
        json_body={"by": by},
        on_status={404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"resumed {chunk_id} — its worker resumes in place")


@chunk_group.command("detach", cls=FleetCommand)
@click.argument("chunk_id")
def chunk_detach(cli: CliContext, chunk_id: str) -> None:
    """Forcibly release CHUNK from its runner.

    A pure client of the hub API: ``POST /api/chunks/{id}/detach``. The chunk re-derives
    ready and is re-claimable at its current node; the holding runner releases it on its
    next tick. 409 when the chunk has no live route to release."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/detach",
        "POST /chunks/{id}/detach",
        on_status={409: "chunk has no live route", 404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"detached {chunk_id} — released from its runner, re-claimable at its current node")


@chunk_group.command("requeue", cls=FleetCommand)
@click.argument("chunk_id")
def chunk_requeue(cli: CliContext, chunk_id: str) -> None:
    """Close an escalation by supersession: requeue CHUNK at its current node."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/requeues",
        "POST /chunks/{id}/requeues",
        on_status={409: "chunk is not escalated", 404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"requeued {chunk_id} — re-leasable at its current node")


@chunk_group.command("stop", cls=FleetCommand)
@click.argument("chunk_id")
@click.option("--by", "by", default="operator", help="Who is stopping (recorded on the fact).")
def chunk_stop(cli: CliContext, chunk_id: str, by: str) -> None:
    """Terminally abandon CHUNK — the operator's last-resort verb (issue #118).

    A pure client of ``POST /api/chunks/{id}/stop``. The chunk derives ``stopped`` and
    never re-derives ``ready``; any live route is released and any open escalation closed
    in the same operation. 409 when already done/stopped. There is no ``un-stop``."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/stop",
        "POST /chunks/{id}/stop",
        json_body={"by": by},
        on_status={409: "chunk is not stoppable", 404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"stopped {chunk_id} — terminally abandoned, its route (if any) released")


@chunk_group.command("done", cls=FleetCommand)
@click.argument("chunk_id")
@click.option("--by", "by", default="operator", help="Who is completing (recorded on the fact).")
def chunk_done(cli: CliContext, chunk_id: str, by: str) -> None:
    """Manually complete CHUNK, from any non-``done`` status, including ``stopped`` (issue #294).
    A pure client of ``POST /api/chunks/{id}/complete``. The chunk derives ``done``; any live
    route and held hub-exec slot are released in the same operation, and its work refs become
    eligible for closure. Idempotent — an already-``done`` chunk is a harmless no-op, never
    refused."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/complete",
        "POST /chunks/{id}/complete",
        json_body={"by": by},
        on_status={404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"completed {chunk_id} — done, its route (if any) released")


@chunk_group.command("restart", cls=FleetCommand)
@click.argument("chunk_id")
@click.option(
    "--to-graph",
    default=None,
    help="Move CHUNK onto this graph as part of the same move — a graph id, or a name resolved to the "
    "newest enabled mint of it. Omit to restart CHUNK where it stands; naming its own pin is refused.",
)
@click.option(
    "--node",
    default=None,
    help="Node name to force CHUNK onto, on --to-graph's graph when one is given and CHUNK's own "
    "otherwise. Omit for its current node's name, or that graph's entry node if CHUNK has never moved.",
)
@click.option("--by", "by", default="operator", help="Who is restarting (recorded on the fact).")
def chunk_restart(cli: CliContext, chunk_id: str, to_graph: str | None, node: str | None, by: str) -> None:
    """Force CHUNK onto a node now, on a freshly minted session (issues #370, #371).

    A pure client of ``POST /api/chunks/{id}/restart``. The move has already happened when the call
    returns: the bumped epoch tears the running attempt down and re-enters, where ``migrate`` only
    records an intent for the next transition. 409 when CHUNK is terminal or the target refuses it."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/restart",
        "POST /chunks/{id}/restart",
        json_body={"node": node, "to_graph": to_graph, "by": by},
        on_status={409: "chunk is not restartable there", 404: f"no such chunk {chunk_id}"},
    )
    body = resp.json()
    landed = body.get("current_node_name") or node or "its current node"
    onto = f" on graph `{to_graph}`" if to_graph is not None else ""
    cli.show_lines(body, f"restarted {chunk_id} at `{landed}`{onto} — re-entering on a fresh session")


@chunk_group.command("migrate", cls=FleetCommand)
@click.argument("chunk_id")
@click.option("--to-graph", default=None, help="Migration target — a graph id or name. Required unless --cancel.")
@click.option(
    "--node",
    default=None,
    help="Force landing on this node name on the target graph (forced mode). Omit for auto (name-matched).",
)
@click.option("--cancel", is_flag=True, default=False, help="Clear the chunk's standing migration intent.")
def chunk_migrate(cli: CliContext, chunk_id: str, to_graph: str | None, node: str | None, cancel: bool) -> None:
    """Set, overwrite, or clear CHUNK's standing migration intent (issue #124).

    ``--node`` present selects ``forced``, absent selects ``auto``; ``--cancel`` clears
    a standing intent and conflicts with ``--to-graph``/``--node``. The intent is consulted at
    the chunk's next transition, never applied eagerly — ``restart --to-graph`` moves it now."""
    if cancel and (to_graph is not None or node is not None):
        raise click.UsageError("--cancel cannot be combined with --to-graph/--node")
    if not cancel and to_graph is None:
        raise click.UsageError("--to-graph is required unless --cancel")

    if cancel:
        body: dict[str, object] = {"intended_migration": None}
    else:
        assert to_graph is not None, "checked above: --to-graph is required unless --cancel"
        intended: dict[str, str] = {"to_graph": to_graph}
        if node is not None:
            intended["node"] = node
        body = {"intended_migration": intended}

    resp = cli.patch(
        f"/api/chunks/{chunk_id}",
        "PATCH /chunks/{id}",
        json_body=body,
        on_status={
            404: f"unknown chunk {chunk_id}",
            409: "chunk is not editable",
            422: "invalid migration request",
        },
    )

    body = resp.json()
    cli.show(body, MigrationIntent(chunk_id, body, cancelled=cancel))


@chunk_group.command("group", cls=FleetCommand)
@click.argument("chunk_id")
@click.argument("merge_ids", nargs=-1, required=True)
def chunk_group_cmd(cli: CliContext, chunk_id: str, merge_ids: tuple[str, ...]) -> None:
    """Merge MERGE_IDS into CHUNK_ID, the survivor.

    A pure client of ``POST /api/chunks/{id}/group``: the survivor and every merge id
    must currently be **unacquired** — ``not_ready`` or ``ready``, in any mix (409
    otherwise). The survivor absorbs the union of work refs and keeps its own status."""
    resp = cli.post(
        f"/api/chunks/{chunk_id}/group",
        "POST /chunks/{id}/group",
        json_body={"merge_chunk_ids": list(merge_ids)},
        on_status={404: f"unknown chunk {chunk_id}", 409: "one of the named chunks is not unacquired"},
    )
    body = resp.json()
    merged = ", ".join(body.get("merged_chunk_ids", [])) or "none"
    cli.show_lines(body, f"grouped into {body['chunk_id']} (merged: {merged})")


@dataclass(frozen=True)
class WorkItems:
    """One chunk's work items, read and rendered — the body ``work-items`` and its deprecated
    ``pm`` alias share, since a ``cls=``-built verb cannot be reached through ``Context.invoke``."""

    cli: CliContext
    chunk_id: str

    def show(self) -> None:
        resp = self.cli.get(
            f"/api/chunks/{self.chunk_id}/work-items",
            "GET /chunks/{id}/work-items",
            on_status={404: f"unknown chunk {self.chunk_id}"},
        )
        body = resp.json()
        self.cli.show(body, WorkItemListing(body.get("items", [])))


@chunk_group.command("work-items", cls=FleetCommand)
@click.argument("chunk_id")
def chunk_work_items(cli: CliContext, chunk_id: str) -> None:
    """CHUNK's work items, pass-through — one entry per work ref, vendor-native.

    A pure client of ``GET /api/chunks/{id}/work-items``; a per-pointer forge failure
    degrades to that entry's own ``error`` rather than failing the whole read."""
    WorkItems(cli, chunk_id).show()


@chunk_group.command("pm", hidden=True, cls=FleetCommand)
@click.argument("chunk_id")
def chunk_pm(cli: CliContext, chunk_id: str) -> None:
    """Deprecated alias for ``blizzard hub chunk work-items`` (issue #55)."""
    click.echo(
        "warning: `blizzard hub chunk pm` is deprecated — use `blizzard hub chunk work-items`",
        err=True,
    )
    WorkItems(cli, chunk_id).show()


# `blizzard hub item` — blizzard#361


class WorkToken(click.ParamType):
    """A work-item token as typed (``hub:42``, ``blizzard#123``), parsed once here at
    the CLI edge into the plain ``(source, ref)`` path segments the wire carries — unlike
    ``Pointer``, the token form never rides an item route."""

    name = "token"

    def convert(self, value: str, param: click.Parameter | None, ctx: click.Context | None) -> tuple[str, str]:
        for sep in (":", "#"):
            if sep in value:
                source, _, ref = value.partition(sep)
                source, ref = source.strip(), ref.strip()
                if source and ref:
                    return source, ref
        self.fail(
            f"could not resolve {value!r} to a work source and ref (expected <source>:<ref> or <source>#<ref>)",
            param,
            ctx,
        )


_DEFAULT_ITEM_SOURCE = "hub"


@hub.group("item")
def item_group() -> None:
    """Operator verbs over one work item: author, edit, or withdraw it at its source."""


def _read_body_file(path: str) -> str:
    """PATH's contents, or stdin when PATH is ``-`` (``graph mint`` precedent)."""
    if path == "-":
        return click.get_text_stream("stdin").read()
    try:
        return Path(path).read_text()
    except OSError as exc:
        raise click.ClickException(f"failed to read {path}: {exc}") from exc


@item_group.command("create", cls=FleetCommand)
@click.option("--title", required=True, help="The item's title.")
@click.option("--body-file", "body_file", required=True, help="Path to the item's body, or '-' for stdin.")
@click.option(
    "--priority",
    "priority",
    type=click.Choice(["low", "normal", "high"]),
    default="normal",
    help="Stated priority (default normal).",
)
@click.option(
    "--source",
    "source",
    default=_DEFAULT_ITEM_SOURCE,
    help=f"Work source to author at (default {_DEFAULT_ITEM_SOURCE!r}, the one source with an editor).",
)
def item_create(cli: CliContext, title: str, body_file: str, priority: str, source: str) -> None:
    """Author a fresh item at SOURCE, minting its resting chunk.

    --body-file may be '-' to read the body from stdin, so an agent can pipe a composed
    spec without shell-quoting a multi-line markdown document."""
    body = _read_body_file(body_file)
    resp = cli.send(
        "post",
        f"/api/work-sources/{source}/items",
        json_body={"title": title, "body": body, "stated_priority": priority},
    )
    if resp.status_code == httpx.codes.CONFLICT:
        conflict = resp.json()
        if "existing_chunk_id" in conflict:
            raise click.ClickException(
                f"pointer {conflict.get('source')}#{conflict.get('ref')} already held by "
                f"chunk {conflict.get('existing_chunk_id')}"
            )
    cli.check(
        resp,
        "POST /work-sources/{source}/items",
        on_status={404: f"unknown work source {source!r}", 409: f"work source {source!r} has no editor"},
    )
    body_json = resp.json()
    cli.show_lines(body_json, f"created {body_json['label']} → chunk {body_json['chunk_id']}")


@item_group.command("edit", cls=FleetCommand)
@click.argument("token", type=WorkToken())
@click.option("--title", default=None, help="Replace the title.")
@click.option("--body-file", "body_file", default=None, help="Replace the body from a path, or '-' for stdin.")
@click.option(
    "--priority", "priority", type=click.Choice(["low", "normal", "high"]), default=None, help="Replace the priority."
)
def item_edit(
    cli: CliContext, token: tuple[str, str], title: str | None, body_file: str | None, priority: str | None
) -> None:
    """Edit the item at TOKEN (e.g. hub:42) in place — only the given fields change."""
    source, ref = token
    json_body: dict[str, object] = {}
    if title is not None:
        json_body["title"] = title
    if body_file is not None:
        json_body["body"] = _read_body_file(body_file)
    if priority is not None:
        json_body["stated_priority"] = priority
    resp = cli.patch(
        f"/api/work-sources/{source}/items/{ref}",
        "PATCH /work-sources/{source}/items/{ref}",
        json_body=json_body,
        on_status={404: f"unknown {source}:{ref}", 409: f"work source {source!r} has no editor"},
    )
    body_json = resp.json()
    cli.show_lines(body_json, f"edited {body_json['label']}")


@item_group.command("delete", cls=FleetCommand)
@click.argument("token", type=WorkToken())
@click.option("--yes", is_flag=True, default=False, help="Skip the confirmation prompt.")
def item_delete(cli: CliContext, token: tuple[str, str], yes: bool) -> None:
    """Withdraw the item at TOKEN (e.g. hub:42)."""
    source, ref = token
    if not yes and not click.confirm(f"withdraw {source}:{ref}?"):
        raise click.Abort()
    resp = cli.delete(
        f"/api/work-sources/{source}/items/{ref}",
        "DELETE /work-sources/{source}/items/{ref}",
        on_status={404: f"unknown {source}:{ref}", 409: f"work source {source!r} has no editor"},
    )
    body_json = resp.json()
    cli.show_lines(body_json, f"withdrew {body_json['label']}")


# `blizzard hub runner` — issue #104 (issue #86a: enroll)


@hub.group("runner")
def runner_group() -> None:
    """Operator verbs over one runner: identity, liveness, and its pause brake."""


@runner_group.command("list", cls=FleetCommand)
def runner_list(cli: CliContext) -> None:
    """The fleet registry — every runner with derived liveness + paused state."""
    body = cli.get("/api/runners", "GET /runners").json()
    cli.show(body, RunnerListing(body.get("runners", [])))


@runner_group.command("show", cls=FleetCommand)
@click.argument("runner_id")
def runner_show(cli: CliContext, runner_id: str) -> None:
    """One runner's derived liveness + paused state, symmetric with ``runner list``."""
    resp = cli.get(f"/api/runners/{runner_id}", "GET /runners/{id}", on_status={404: f"unknown runner {runner_id}"})
    body = resp.json()
    cli.show(body, RunnerDetail(body))


@runner_group.command("pause", cls=FleetCommand)
@click.argument("runner_id")
@click.option("--by", "by", default="operator", help="Who is pausing (recorded on the fact).")
def runner_pause(cli: CliContext, runner_id: str, by: str) -> None:
    """Pause a runner — it stops claiming new work; in-flight chunks run on."""
    _set_runner_pause(cli, runner_id, verb="pause", by=by)


@runner_group.command("resume", cls=FleetCommand)
@click.argument("runner_id")
@click.option("--by", "by", default="operator", help="Who is resuming (recorded on the fact).")
def runner_resume(cli: CliContext, runner_id: str, by: str) -> None:
    """Resume a paused runner — it claims work again on its next pull."""
    _set_runner_pause(cli, runner_id, verb="resume", by=by)


def _set_runner_pause(cli: CliContext, runner_id: str, *, verb: str, by: str) -> None:
    resp = cli.post(
        f"/api/runners/{runner_id}/{verb}",
        f"POST /runners/{{id}}/{verb}",
        json_body={"by": by},
        on_status={404: f"unknown runner {runner_id}"},
    )
    body = resp.json()
    state = "paused" if body.get("hub_paused") else "running"
    lines = [f"runner {runner_id} is now {state} (at the hub)"]
    if body.get("locally_paused"):
        # Resuming here cannot clear the runner's own brake, so don't imply it did.
        lines.append(f"note: runner {runner_id} also paused itself — clear that with `blizzard runner start`")
    cli.show_lines(body, *lines)


@runner_group.command("enroll", cls=FleetCommand)
@click.argument("runner_id")
def runner_enroll(cli: CliContext, runner_id: str) -> None:
    """Mint (or rotate) RUNNER_ID's bearer token; prints the plaintext exactly once.

    A thin client of ``POST /runners/{id}/enrollments`` (issue #86a). Re-running
    rotates: the old token stops resolving immediately. RUNNER_ID must already be
    registered at the hub (404 otherwise)."""
    resp = cli.post(
        f"/api/runners/{runner_id}/enrollments",
        "POST /runners/{id}/enrollments",
        on_status={404: f"unknown runner {runner_id}"},
    )
    body = resp.json()
    cli.show_lines(body, f"enrolled {runner_id} — bearer token (copy now, shown only once):\n{body['token']}")


# `blizzard hub graph` — issue #101, issue #104, issue #123


@hub.group("graph")
def graph_group() -> None:
    """Operator verbs over minted graphs: list, inspect, mint, retire, re-enable."""


@graph_group.command("list", cls=FleetCommand)
def graph_list(cli: CliContext) -> None:
    """List every minted graph, newest first — name, graph_id, effective, retired."""
    rows = cli.get("/api/graphs", "GET /graphs").json()
    cli.show(rows, GraphListing(rows))


@graph_group.command("show", cls=FleetCommand)
@click.argument("graph_id")
def graph_show(cli: CliContext, graph_id: str) -> None:
    """One graph's full reified definition — nodes and edges."""
    resp = cli.get(f"/api/graphs/{graph_id}", "GET /graphs/{id}", on_status={404: f"unknown graph {graph_id}"})
    body = resp.json()
    cli.show(body, GraphDetail(body))


@graph_group.command("mint", cls=FleetCommand)
@click.argument("path")
def graph_mint(cli: CliContext, path: str) -> None:
    """Mint a graph from PATH's YAML definition; PATH may be '-' to read stdin.

    A file PATH inlines file references relative to its own directory: a 'prompt'/'prompt_addendum' value only when it
    reads as a path, so literal prose stays literal, but every 'artifacts:' value always — one that fails to resolve
    fails the load, naming the entry. Stdin has no directory, so it posts verbatim; a 422 renders in full."""
    if path == "-":
        definition_yaml = click.get_text_stream("stdin").read()
    else:
        try:
            definition_yaml = GraphFile(Path(path)).inlined_yaml
        except (yaml.YAMLError, OSError, ValueError) as exc:
            raise click.ClickException(f"failed to load {path}: {exc}") from exc

    resp = cli.send("post", "/api/graphs", json_body={"definition_yaml": definition_yaml})
    if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        report = resp.json()
        lines = [f"error: {e}" for e in report.get("errors", [])]
        lines += [f"warning: {w}" for w in report.get("warnings", [])]
        raise click.ClickException("graph definition invalid:\n" + "\n".join(lines))
    cli.check(resp, "POST /graphs")
    body = resp.json()
    warnings = [f"warning: {w}" for w in body.get("warnings", [])]
    cli.show_lines(body, f"minted graph {body['graph_id']}", *warnings)


@graph_group.command("sync", cls=FleetCommand)
def graph_sync(cli: CliContext) -> None:
    """Reconcile the hub's packaged graphs into its store, minting only what changed.

    The deploy verb (issue #146) — graphs live in the store, not on disk, so run it at
    the end of every deploy; it is idempotent. The **hub's own** packaged set is what is
    reconciled, not this CLI's. Exits non-zero only if a packaged graph failed to load."""
    resp = cli.post("/api/graphs/sync", "POST /graphs/sync", json_body={})
    body = resp.json()
    cli.show(body, GraphSyncListing(body.get("entries", [])))
    if not body.get("ok", True):
        raise click.ClickException("one or more packaged graphs failed to reconcile")


@graph_group.command("retire", cls=FleetCommand)
@click.argument("graph_id")
@click.option("--by", "by", default="operator", help="Who is retiring (recorded on the fact).")
def graph_retire(cli: CliContext, graph_id: str, by: str) -> None:
    """Retire GRAPH_ID — excludes it from name resolution; in-flight chunks run on."""
    _set_graph_lifecycle(cli, graph_id, verb="retire", by=by)


@graph_group.command("enable", cls=FleetCommand)
@click.argument("graph_id")
@click.option("--by", "by", default="operator", help="Who is re-enabling (recorded on the fact).")
def graph_enable(cli: CliContext, graph_id: str, by: str) -> None:
    """Re-enable a retired GRAPH_ID — restores normal newest-per-name derivation."""
    _set_graph_lifecycle(cli, graph_id, verb="enable", by=by)


@graph_group.command("follow-latest", cls=FleetCommand)
@click.argument("graph_id")
@click.argument("value", type=click.Choice(["true", "false", "inherit"]))
@click.option("--by", "by", default="operator", help="Who is setting the policy (recorded on the fact).")
def graph_follow_latest(cli: CliContext, graph_id: str, value: str, by: str) -> None:
    """Set GRAPH_ID's follow-latest policy: true, false, or inherit (issue #164).

    With the policy on, a chunk pinned to this mint re-pins to the newest enabled mint
    of the same *name* at its next transition. ``inherit`` (the stored ``null``, and
    every mint's default) defers to the hub's own ``follow_latest``."""
    follow_latest = None if value == "inherit" else value == "true"
    resp = cli.post(
        f"/api/graphs/{graph_id}/follow-latest",
        "POST /graphs/{id}/follow-latest",
        json_body={"follow_latest": follow_latest, "by": by},
        on_status={404: f"unknown graph {graph_id}"},
    )
    body = resp.json()
    stored = body.get("follow_latest")
    rendered = "inherit (the hub default)" if stored is None else str(stored).lower()
    cli.show_lines(body, f"graph {graph_id} follow-latest is now {rendered}")


def _set_graph_lifecycle(cli: CliContext, graph_id: str, *, verb: str, by: str) -> None:
    resp = cli.post(
        f"/api/graphs/{graph_id}/{verb}",
        f"POST /graphs/{{id}}/{verb}",
        json_body={"by": by},
        on_status={404: f"unknown graph {graph_id}"},
    )
    body = resp.json()
    state = "retired" if body.get("retired") else "enabled"
    cli.show_lines(body, f"graph {graph_id} is now {state}")


# `blizzard hub queue` — issue #87, issue #104


@hub.group("queue")
def queue_group() -> None:
    """Operator verbs over the ready queue: show its order, replace it, move one chunk."""


@queue_group.command("show", cls=FleetCommand)
def queue_show(cli: CliContext) -> None:
    """The hub-ordered ready queue, read-only — a client of ``GET /api/queue``."""
    body = cli.get("/api/queue", "GET /queue").json()
    cli.show(body, QueueListing(body.get("entries", [])))


@queue_group.command("set", cls=FleetCommand)
@click.argument("chunk_ids", nargs=-1, required=True)
def queue_set(cli: CliContext, chunk_ids: tuple[str, ...]) -> None:
    """Replace the whole ready-queue order with CHUNK_IDS, front to back.

    A pure client of ``PUT /api/queue`` — an idempotent whole-order replacement
    (issue #104). Every id must be in the ready list, not the not_ready backlog (409),
    and must not repeat (422); a ready chunk not named keeps its relative order,
    appended after the named ones."""
    resp = cli.put(
        "/api/queue",
        "PUT /queue",
        json_body={"chunk_ids": list(chunk_ids)},
        on_status={
            409: "one of the named chunks is not in the ready list (it may be in the not_ready backlog instead)",
            422: "chunk_ids must not repeat",
        },
    )
    body = resp.json()
    cli.show_lines(body, f"queue order set ({len(body.get('entries', []))} ready chunk(s))")


@queue_group.command("move", cls=FleetCommand)
@click.argument("chunk_id")
@click.argument("position", type=int)
def queue_move(cli: CliContext, chunk_id: str, position: int) -> None:
    """Move CHUNK_ID to POSITION in the ready queue (``0`` is the front).

    A client of the single-chunk fractional ``POST /api/queue/position`` (issue #137):
    reads the current order, drops CHUNK_ID out of it, clamps POSITION into what's left,
    and sends one anchor. 409 when CHUNK_ID is not in the ready list (it may be in the
    not_ready backlog instead)."""
    peek = cli.get("/api/queue", "GET /queue")
    rest = [entry["chunk_id"] for entry in peek.json().get("entries", []) if entry["chunk_id"] != chunk_id]
    index = min(max(position, 0), len(rest))
    after_chunk_id = rest[index - 1] if index > 0 else None
    resp = cli.post(
        "/api/queue/position",
        "POST /queue/position",
        json_body={"chunk_id": chunk_id, "after_chunk_id": after_chunk_id},
        on_status={409: f"chunk {chunk_id} is not in the ready list (it may be in the not_ready backlog instead)"},
    )
    cli.finish(resp, f"moved {chunk_id} to position {position}")


# `blizzard hub decision` — issue #104


@hub.group("decision")
def decision_group() -> None:
    """Operator verbs over open gate decisions: list, resolve."""


@decision_group.command("list", cls=FleetCommand)
def decision_list(cli: CliContext) -> None:
    """List open decisions awaiting a human (gate surfacing)."""
    body = cli.get("/api/decisions", "GET /decisions").json()
    cli.show(body, DecisionListing(body.get("decisions", [])))


@decision_group.command("resolve", cls=FleetCommand)
@click.argument("decision_id")
@click.argument("choice")
@click.option("--by", "resolved_by", default="operator", help="Who is resolving (recorded on the resolution).")
def decision_resolve(cli: CliContext, decision_id: str, choice: str, resolved_by: str) -> None:
    """Resolve an open decision by picking CHOICE (first-write-wins).

    A pure client of ``POST /api/decisions/{id}/resolutions`` (issue #104's pluralized
    resolution route)."""
    resp = cli.send(
        "post", f"/api/decisions/{decision_id}/resolutions", json_body={"choice": choice, "resolved_by": resolved_by}
    )
    if resp.status_code == httpx.codes.CONFLICT:
        winner = resp.json()
        raise click.ClickException(f"already resolved by {winner.get('already_resolved_by')}")
    cli.check(
        resp,
        "POST /decisions/{id}/resolutions",
        on_status={404: f"no such decision {decision_id}", 400: "invalid choice", 422: "invalid choice"},
    )
    body = resp.json()
    cli.show_lines(body, f"decision {decision_id} resolved: {body['choice']} (by {body['resolved_by']})")


# `blizzard hub question` — issue #104


@hub.group("question")
def question_group() -> None:
    """Operator verbs over open questions: list, answer."""


@question_group.command("list", cls=FleetCommand)
def question_list(cli: CliContext) -> None:
    """Every open (unanswered) question across the fleet."""
    rows = cli.get("/api/questions", "GET /questions").json()
    cli.show(rows, QuestionListing(rows))


@question_group.command("answer", cls=FleetCommand)
@click.argument("question_id")
@click.argument("answer_text")
@click.option("--by", "answered_by", default="operator", help="Who is answering (recorded on the row).")
def question_answer(cli: CliContext, question_id: str, answer_text: str, answered_by: str) -> None:
    """Answer an open question (first-write-wins CAS at the hub).

    A racing second answer loses and is told who already answered. A pure client of
    ``POST /api/questions/{id}/answers`` (issue #104)."""
    resp = cli.send(
        "post", f"/api/questions/{question_id}/answers", json_body={"answer": answer_text, "answered_by": answered_by}
    )
    if resp.status_code == httpx.codes.CONFLICT:
        winner = resp.json()
        raise click.ClickException(f"already answered by {winner.get('answered_by')}: {winner.get('answer')!r}")
    cli.check(resp, "POST /questions/{id}/answers", on_status={404: f"unknown question {question_id}"})
    cli.finish(resp, f"answered {question_id}: {answer_text!r} (the runner will resume the session)")


# `blizzard hub analytics` — blizzard#254


@hub.group("analytics")
def analytics_group() -> None:
    """Operator verbs over derived transcript-event analytics."""


@analytics_group.command("re-derive", cls=FleetCommand)
@click.option("--segment", "segment_id", default=None, help="Force one segment, regardless of its candidacy.")
@click.option("--chunk", "chunk_id", default=None, help="Every candidate segment of one chunk.")
@click.option("--limit", "limit", default=50, show_default=True, help="Cap on segments derived by one call.")
def analytics_re_derive(cli: CliContext, segment_id: str | None, chunk_id: str | None, limit: int) -> None:
    """Force the standing derivation sweep's own replacement unit now, rather than
    waiting for its next tick — scoped to one segment, one chunk, or every candidate
    (neither option given). No downtime: it runs the same in-process reconciler already
    live, just driven directly. Prints ``derived``/``remaining``; a nonzero ``remaining``
    on a chunk/all-scoped call means running it again continues from where it left off."""
    if segment_id is not None and chunk_id is not None:
        raise click.ClickException("--segment and --chunk are mutually exclusive")
    body: dict[str, object] = {"limit": limit}
    if segment_id is not None:
        body["segment_id"] = segment_id
    if chunk_id is not None:
        body["chunk_id"] = chunk_id
    resp = cli.post("/api/analytics/re-derive", "POST /analytics/re-derive", json_body=body)
    result = resp.json()
    cli.show_lines(result, f"derived {result['derived']}, {result['remaining']} remaining in scope")


# `blizzard hub analytics events`/`summary` — shared filter options (blizzard#257 D2): one
# declaration per flag, so each verb's command stacks whichever of them its dataset(s) take.


def _graph_option(f: Any) -> Any:
    return click.option("--graph", "graph_id", default=None, help="Scope to one workflow graph.")(f)


def _source_option(f: Any) -> Any:
    return click.option("--source", default=None, help="Scope to one work source.")(f)


def _since_option(f: Any) -> Any:
    return click.option(
        "--since",
        default=None,
        type=click.DateTime(),
        help="Only records at/after this instant, read in the operator's own local time.",
    )(f)


def _until_option(f: Any) -> Any:
    return click.option(
        "--until",
        default=None,
        type=click.DateTime(),
        help="Only records before this instant, read in the operator's own local time.",
    )(f)


def _extractor_version_option(f: Any) -> Any:
    return click.option(
        "--extractor-version",
        "extractor_version",
        default=None,
        help="The event-extraction version to read (defaults to the current version).",
    )(f)


def _kind_option(f: Any) -> Any:
    return click.option("--kind", default=None, help="Narrow to one event kind.")(f)


def _tool_option(f: Any) -> Any:
    return click.option("--tool", default=None, help="Narrow to one tool name.")(f)


def _subject_prefix_option(f: Any) -> Any:
    return click.option(
        "--subject-prefix", "subject_prefix", default=None, help="Narrow to subjects with this prefix."
    )(f)


def _node_option(f: Any) -> Any:
    return click.option("--node", "node_id", default=None, help="Narrow to one node id.")(f)


def _utc_query_value(value: datetime | None) -> str | None:
    """D6: a bare ``--since``/``--until`` is read as the operator's own local wall clock,
    not UTC — converted (not merely relabeled) before it crosses the wire."""
    return iso_utc(value.astimezone(UTC)) if value is not None else None


def _scope_params(
    *,
    graph_id: str | None,
    source: str | None,
    since: datetime | None,
    until: datetime | None,
    extractor_version: str | None = None,
    kind: str | None = None,
    tool: str | None = None,
    subject_prefix: str | None = None,
    node_id: str | None = None,
) -> dict[str, str]:
    """Every named filter as a query param, omitting whichever were left unset."""
    named = {
        "graph_id": graph_id,
        "source": source,
        "since": _utc_query_value(since),
        "until": _utc_query_value(until),
        "extractor_version": extractor_version,
        "kind": kind,
        "tool": tool,
        "subject_prefix": subject_prefix,
        "node_id": node_id,
    }
    return {k: v for k, v in named.items() if v is not None}


@analytics_group.command("events", cls=FleetCommand)
@_graph_option
@_source_option
@_since_option
@_until_option
@_extractor_version_option
@_kind_option
@_tool_option
@_subject_prefix_option
@_node_option
@click.option("--cursor", default=None, help="Resume from a prior page's next_cursor.")
@click.option(
    "--limit", default=None, type=int, help="Max rows in one page (1-1000, default 200). Illegal with --ndjson."
)
@click.option(
    "--ndjson",
    is_flag=True,
    default=False,
    help="Stream every matching event as NDJSON to stdout, unpaged. Incompatible with --json/--cursor/--limit.",
)
def analytics_events(
    cli: CliContext,
    graph_id: str | None,
    source: str | None,
    since: datetime | None,
    until: datetime | None,
    extractor_version: str | None,
    kind: str | None,
    tool: str | None,
    subject_prefix: str | None,
    node_id: str | None,
    cursor: str | None,
    limit: int | None,
    ndjson: bool,
) -> None:
    """Read the derived transcript-event projection (blizzard#255), every ``/events``
    filter as a flag: a bounded page by default, or the whole filtered set streamed as
    NDJSON to stdout under ``--ndjson``."""
    if ndjson:
        if cli.as_json:
            raise click.UsageError("--ndjson is incompatible with --json")
        if cursor is not None:
            raise click.UsageError("--ndjson is incompatible with --cursor")
        if limit is not None:
            raise click.UsageError("--ndjson is incompatible with --limit")
    params = _scope_params(
        graph_id=graph_id,
        source=source,
        since=since,
        until=until,
        extractor_version=extractor_version,
        kind=kind,
        tool=tool,
        subject_prefix=subject_prefix,
        node_id=node_id,
    )
    if ndjson:
        for line in cli.stream("/api/analytics/events/ndjson", "GET /analytics/events/ndjson", params=params):
            click.echo(line)
        return
    if cursor is not None:
        params["cursor"] = cursor
    params["limit"] = str(limit if limit is not None else 200)
    body = cli.get("/api/analytics/events", "GET /analytics/events", params=params).json()
    cli.show(body, EventListing(body["events"]))


# `blizzard hub analytics summary` — blizzard#257 D1/D2: one verb over the ten read
# rollup routes, an explicit dataset→route table (a `-`↔`/` rule would 404 on
# `agent-types`), and an explicit per-dataset filter-applicability table — a filter
# inapplicable to the chosen dataset is refused, never silently dropped.

#: The four scope filters every dataset takes — the common ground D2 builds each
#: dataset's own applicable set on top of.
_SCOPE_FILTERS = frozenset({"graph_id", "source", "since", "until"})

#: The flag each filter's dest name renders as, for a per-dataset applicability error.
_FLAG_NAMES = {
    "graph_id": "--graph",
    "source": "--source",
    "since": "--since",
    "until": "--until",
    "extractor_version": "--extractor-version",
    "kind": "--kind",
    "tool": "--tool",
    "subject_prefix": "--subject-prefix",
    "node_id": "--node",
}


@dataclass(frozen=True)
class _Dataset:
    """One ``summary`` choice: its route, the key its envelope carries the rows under,
    the view that renders them, and which of the shared filters it honors. Only
    ``spend-chunks`` paginates or streams — the one rollup with a bulk-export sibling."""

    path: str
    response_key: str
    view: type[Listing]
    filters: frozenset[str]
    paginated: bool = False
    streamable: bool = False


#: Mints the ten read rollup routes' own criteria types, mirroring each route's own
#: declared query params (``api/analytics.py``) — not derived from the path, so a
#: hyphenated route (``counts/agent-types``) never round-trips through a naive rule.
_DATASETS: dict[str, _Dataset] = {
    "counts-files": _Dataset(
        "/api/analytics/counts/files",
        "counts",
        CountsListing,
        _SCOPE_FILTERS | {"extractor_version", "tool", "subject_prefix", "node_id"},
    ),
    "counts-skills": _Dataset(
        "/api/analytics/counts/skills", "counts", CountsListing, _SCOPE_FILTERS | {"extractor_version", "node_id"}
    ),
    "counts-agent-types": _Dataset(
        "/api/analytics/counts/agent-types",
        "counts",
        CountsListing,
        _SCOPE_FILTERS | {"extractor_version", "kind", "tool", "subject_prefix", "node_id"},
    ),
    "counts-nodes": _Dataset(
        "/api/analytics/counts/nodes",
        "counts",
        CountsListing,
        _SCOPE_FILTERS | {"extractor_version", "kind", "tool", "subject_prefix"},
    ),
    "durations-nodes": _Dataset("/api/analytics/durations/nodes", "durations", DurationsListing, _SCOPE_FILTERS),
    "durations-graphs": _Dataset("/api/analytics/durations/graphs", "durations", DurationsListing, _SCOPE_FILTERS),
    "spend-nodes": _Dataset("/api/analytics/spend/nodes", "spend", SpendListing, _SCOPE_FILTERS),
    "spend-graphs": _Dataset("/api/analytics/spend/graphs", "spend", SpendListing, _SCOPE_FILTERS),
    "spend-chunks": _Dataset(
        "/api/analytics/spend/chunks",
        "spend",
        ChunkSpendListing,
        _SCOPE_FILTERS,
        paginated=True,
        streamable=True,
    ),
    "outcomes-nodes": _Dataset("/api/analytics/outcomes/nodes", "outcomes", OutcomesListing, _SCOPE_FILTERS),
}

_SPEND_CHUNKS_NDJSON_PATH = "/api/analytics/spend/chunks/ndjson"


@analytics_group.command("summary", cls=FleetCommand)
@click.argument("dataset", type=click.Choice(sorted(_DATASETS)))
@_graph_option
@_source_option
@_since_option
@_until_option
@_extractor_version_option
@_kind_option
@_tool_option
@_subject_prefix_option
@_node_option
@click.option("--cursor", default=None, help="Resume from a prior page's next_cursor (spend-chunks only).")
@click.option(
    "--limit", default=None, type=int, help="Max rows in one page (spend-chunks only). Illegal with --ndjson."
)
@click.option(
    "--ndjson",
    is_flag=True,
    default=False,
    help="Stream spend-chunks as NDJSON to stdout, unpaged (spend-chunks only). "
    "Incompatible with --json/--cursor/--limit.",
)
def analytics_summary(
    cli: CliContext,
    dataset: str,
    graph_id: str | None,
    source: str | None,
    since: datetime | None,
    until: datetime | None,
    extractor_version: str | None,
    kind: str | None,
    tool: str | None,
    subject_prefix: str | None,
    node_id: str | None,
    cursor: str | None,
    limit: int | None,
    ndjson: bool,
) -> None:
    """Read one of the canned counts or operational-dataset rollups (blizzard#255/#256):
    the four DATASET counts (counts-files, counts-skills, counts-agent-types,
    counts-nodes) and the six operational datasets (durations-nodes, durations-graphs,
    spend-nodes, spend-graphs, spend-chunks, outcomes-nodes). Only spend-chunks pages
    or streams; a filter DATASET's own route does not expose is refused."""
    spec = _DATASETS[dataset]
    given = {
        "graph_id": graph_id,
        "source": source,
        "since": since,
        "until": until,
        "extractor_version": extractor_version,
        "kind": kind,
        "tool": tool,
        "subject_prefix": subject_prefix,
        "node_id": node_id,
    }
    for name, value in given.items():
        if value is not None and name not in spec.filters:
            raise click.UsageError(f"{_FLAG_NAMES[name]} does not apply to dataset {dataset!r}")
    if cursor is not None and not spec.paginated:
        raise click.UsageError(f"--cursor does not apply to dataset {dataset!r}")
    if limit is not None and not spec.paginated:
        raise click.UsageError(f"--limit does not apply to dataset {dataset!r}")
    if ndjson and not spec.streamable:
        raise click.UsageError(f"--ndjson does not apply to dataset {dataset!r}")
    if ndjson:
        if cli.as_json:
            raise click.UsageError("--ndjson is incompatible with --json")
        if cursor is not None:
            raise click.UsageError("--ndjson is incompatible with --cursor")
        if limit is not None:
            raise click.UsageError("--ndjson is incompatible with --limit")
    params = _scope_params(
        graph_id=graph_id,
        source=source,
        since=since,
        until=until,
        extractor_version=extractor_version,
        kind=kind,
        tool=tool,
        subject_prefix=subject_prefix,
        node_id=node_id,
    )
    if ndjson:
        for line in cli.stream(_SPEND_CHUNKS_NDJSON_PATH, "GET /analytics/spend/chunks/ndjson", params=params):
            click.echo(line)
        return
    if cursor is not None:
        params["cursor"] = cursor
    if spec.paginated:
        params["limit"] = str(limit if limit is not None else 200)
    operation = f"GET {spec.path.removeprefix('/api')}"
    body = cli.get(spec.path, operation, params=params).json()
    cli.show(body, spec.view(body[spec.response_key]))
