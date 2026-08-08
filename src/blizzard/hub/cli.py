"""``blizzard hub <cmd>`` — the fleet surface.

Client verbs are pure clients of the hub's HTTP API; ``host`` *becomes* the hub
daemon. This module is CLI top-level glue, so ``echo`` for user output is fine here
(``bzh:structlog-logging``). Operator verbs are grouped by noun (issue #104);
``status`` stays top-level as a cross-resource dashboard."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Callable
from pathlib import Path
from types import FrameType

import click
import httpx
import uvicorn
import yaml

from blizzard.cli.host_directory import resolve_host_directory
from blizzard.foundation.store.migrations import RevisionMismatchError
from blizzard.hub import cli_login, session_store
from blizzard.hub.api.marker_auth import _MARKER_TOKEN_HEADER
from blizzard.hub.app import build_hosted_app
from blizzard.hub.cli_context import CLIENT_TIMEOUT, DEFAULT_HUB_URL, ENV_HUB_URL, CliContext
from blizzard.hub.cli_views import (
    ChunkDetail,
    ChunkListing,
    DecisionListing,
    FleetStatus,
    GraphDetail,
    GraphListing,
    GraphSyncListing,
    MigrationIntent,
    QuestionListing,
    QueueListing,
    RunnerDetail,
    RunnerListing,
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


def _hub_url_options(f: Callable[..., object]) -> Callable[..., object]:
    """``--hub-url``, uniform across every operator verb."""
    f = click.option(
        "--hub-url", "hub_url", default=None, help=f"Hub API base URL (default ${ENV_HUB_URL} or {DEFAULT_HUB_URL})."
    )(f)
    return f


def _json_option(f: Callable[..., object]) -> Callable[..., object]:
    """``--json``, uniform across every read and write verb alike (issue #104)."""
    return click.option("--json", "as_json", is_flag=True, default=False, help="Print the raw response body as JSON.")(
        f
    )


# The since-the-beginning-of-time cutoff `hub status` passes ``GET /api/spend``
# for its fleet-total line (issue #60) — a full-fleet overview, not a "today" window.
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
# (see ``_EarlyShutdownServer`` below).
_GRACEFUL_SHUTDOWN_SECONDS = 5


class _EarlyShutdownServer(uvicorn.Server):
    """Sets ``shutdown_signal`` the instant SIGTERM/SIGINT is caught (issue #47).

    ``handle_exit`` runs synchronously before uvicorn's graceful drain, which an SSE
    response never finishes (pinned by ``tests/test_pin_hub_api.py``)."""

    def __init__(self, config: uvicorn.Config, *, shutdown_signal: asyncio.Event) -> None:
        super().__init__(config)
        self._shutdown_signal = shutdown_signal

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        self._shutdown_signal.set()
        super().handle_exit(sig, frame)


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
    directory = resolve_host_directory(directory, dir_option)
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


@hub.command()
@_json_option
@_hub_url_options
def status(as_json: bool, hub_url: str | None) -> None:
    """The fleet view: every chunk with its derived status, the runners, and open questions."""
    cli = CliContext.of(hub_url, as_json)
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


def _parse_pointer(token: str) -> str:
    """The ingest token the CLI hands the hub.

    The CLI carries no pointer grammar of its own; a token travels through verbatim.
    The deprecated ``github:<rest>`` prefix warns on stderr and passes ``rest`` on its
    own merits."""
    if token.startswith("github:"):
        rest = token[len("github:") :]
        click.echo(
            f"warning: the 'github:' pointer prefix is deprecated (in {token!r}) — resolving {rest!r} on its own",
            err=True,
        )
        return rest
    return token


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


@hub.command("rotate-signing-key")
@_hub_url_options
def rotate_signing_key(hub_url: str | None) -> None:
    """Rotate the hub's IdP signing keypair (issue #95) — mints a fresh current key,
    demoting the old current to previous; no restart. A no-op error under ``auth.mode = "none"``
    (no keypair exists). Human-plane, gated on ``user:manage`` — under ``auth.mode =
    "oauth"`` this requires a hub session (``blizzard hub login``, issue #96)."""
    cli = CliContext.of(hub_url)
    cli.post(
        "/api/auth/rotate-signing-key",
        "POST /auth/rotate-signing-key",
        on_status={404: "the IdP surface is not enabled (auth.mode=none)"},
    )
    click.echo("signing key rotated")


@hub.command()
@_hub_url_options
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
def login(hub_url: str | None, paste: bool, no_browser: bool) -> None:
    """Log into the hub (issue #96) — opens the browser to the hub's own authorize
    endpoint (PKCE, an ephemeral ``127.0.0.1`` loopback redirect) and stores the
    resulting session token locally. The CLI never contacts a provider directly.
    ``--paste`` uses the paste-code fallback for a shell with no reachable loopback
    listener; ``--no-browser`` still runs the loopback flow, printing the URL."""
    cli = CliContext.of(hub_url)
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


@hub.command()
@_hub_url_options
def logout(hub_url: str | None) -> None:
    """Log out of the hub (issue #96) — deletes the locally stored session token and
    revokes it at the hub, so it stops resolving even if it leaked. A no-op (locally)
    if never logged in; the revoke call is best-effort (a hub already unreachable, or
    an already-expired session, does not block the local cleanup)."""
    cli = CliContext.of(hub_url)
    with contextlib.suppress(click.ClickException):
        cli.send("post", "/api/auth/logout")
    session_store.SessionFile.of().delete(cli.hub_url)
    click.echo(f"logged out of {cli.hub_url}")


# `blizzard hub chunk` — issue #104


@hub.group("chunk")
def chunk_group() -> None:
    """Operator verbs over one chunk: ingest, inspect, edit, and transition it."""


@chunk_group.command("list")
@_json_option
@_hub_url_options
def chunk_list(as_json: bool, hub_url: str | None) -> None:
    """The fleet chunk list — derived status per chunk."""
    cli = CliContext.of(hub_url, as_json)
    rows = cli.get("/api/chunks", "GET /chunks").json()
    cli.show(rows, ChunkListing(rows))


@chunk_group.command("show")
@click.argument("chunk_id")
@_json_option
@_hub_url_options
def chunk_show(chunk_id: str, as_json: bool, hub_url: str | None) -> None:
    """One chunk's full aggregate — status, current node, route, pointers, cost."""
    cli = CliContext.of(hub_url, as_json)
    resp = cli.get(f"/api/chunks/{chunk_id}", "GET /chunks/{id}", on_status={404: f"unknown chunk {chunk_id}"})
    detail = resp.json()
    cli.show(detail, ChunkDetail(detail))


@chunk_group.command("ingest")
@click.argument("pointers", nargs=-1, required=True)
@_json_option
@_hub_url_options
def chunk_ingest(pointers: tuple[str, ...], as_json: bool, hub_url: str | None) -> None:
    """Ingest work items by token, minting a chunk.

    Each POINTER is a source-native token — ``source:ref``, ``source#ref``, or a pasted
    work item URL; a batch mints one chunk carrying every pointer. 422 when no
    configured work source claims a token; 409 when a pointer is already held."""
    cli = CliContext.of(hub_url, as_json)
    tokens = [_parse_pointer(p) for p in pointers]
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


@chunk_group.command("set")
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
@_json_option
@_hub_url_options
def chunk_set(
    chunk_id: str,
    graph_id: str | None,
    default_model: tuple[str, ...],
    default_effort: str | None,
    as_json: bool,
    hub_url: str | None,
) -> None:
    """Repin CHUNK's graph and/or default model/effort in one call (issues #104, #144).

    A pure client of ``PATCH /api/chunks/{id}``, naming whichever fields were given and
    applied all-or-nothing. At least one option is required; 409 for the defaults once
    CHUNK is claimed, and for ``--graph`` once it is claimed or has moved (#271)."""
    if graph_id is None and not default_model and default_effort is None:
        raise click.UsageError("at least one of --graph/--default-model/--default-effort is required")
    cli = CliContext.of(hub_url, as_json)
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


@chunk_group.command("promote")
@click.argument("chunk_id")
@_json_option
@_hub_url_options
def chunk_promote(chunk_id: str, as_json: bool, hub_url: str | None) -> None:
    """Promote a not-ready CHUNK to ready so a runner may claim it.

    A pure client of the hub API: ``POST /api/chunks/{id}/promote``. Idempotent — promoting
    an already-ready chunk is a harmless no-op; 404 only when the chunk is unknown."""
    cli = CliContext.of(hub_url, as_json)
    resp = cli.post(
        f"/api/chunks/{chunk_id}/promote", "POST /chunks/{id}/promote", on_status={404: f"no such chunk {chunk_id}"}
    )
    cli.finish(resp, f"promoted {chunk_id} — now ready for a runner to claim")


@chunk_group.command("pause")
@click.argument("chunk_id")
@click.option("--by", "by", default="operator", help="Who is pausing (recorded on the fact).")
@_json_option
@_hub_url_options
def chunk_pause(chunk_id: str, by: str, as_json: bool, hub_url: str | None) -> None:
    """Pause CHUNK — the runner kills and parks the worker but keeps the claim (issue #46).

    A pure client of the hub API: ``POST /api/chunks/{id}/pause``. Unlike ``detach``, no
    route is released and no retry is consumed. 409 when the chunk is done/stopped/
    delivering."""
    cli = CliContext.of(hub_url, as_json)
    resp = cli.post(
        f"/api/chunks/{chunk_id}/pause",
        "POST /chunks/{id}/pause",
        json_body={"by": by},
        on_status={409: "chunk is not pausable", 404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"paused {chunk_id} — its worker will be killed and parked, keeping the claim")


@chunk_group.command("resume")
@click.argument("chunk_id")
@click.option("--by", "by", default="operator", help="Who is resuming (recorded on the fact).")
@_json_option
@_hub_url_options
def chunk_resume(chunk_id: str, by: str, as_json: bool, hub_url: str | None) -> None:
    """Resume a paused CHUNK — the runner resumes the parked worker in place (issue #46).

    A pure client of the hub API: ``POST /api/chunks/{id}/resume``. Idempotent: resuming
    an unpaused chunk is a harmless no-op. 404 only when the chunk is unknown."""
    cli = CliContext.of(hub_url, as_json)
    resp = cli.post(
        f"/api/chunks/{chunk_id}/resume",
        "POST /chunks/{id}/resume",
        json_body={"by": by},
        on_status={404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"resumed {chunk_id} — its worker resumes in place")


@chunk_group.command("detach")
@click.argument("chunk_id")
@_json_option
@_hub_url_options
def chunk_detach(chunk_id: str, as_json: bool, hub_url: str | None) -> None:
    """Forcibly release CHUNK from its runner.

    A pure client of the hub API: ``POST /api/chunks/{id}/detach``. The chunk re-derives
    ready and is re-claimable at its current node; the holding runner releases it on its
    next tick. 409 when the chunk has no live route to release."""
    cli = CliContext.of(hub_url, as_json)
    resp = cli.post(
        f"/api/chunks/{chunk_id}/detach",
        "POST /chunks/{id}/detach",
        on_status={409: "chunk has no live route", 404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"detached {chunk_id} — released from its runner, re-claimable at its current node")


@chunk_group.command("requeue")
@click.argument("chunk_id")
@_json_option
@_hub_url_options
def chunk_requeue(chunk_id: str, as_json: bool, hub_url: str | None) -> None:
    """Close an escalation by supersession: requeue CHUNK at its current node."""
    cli = CliContext.of(hub_url, as_json)
    resp = cli.post(
        f"/api/chunks/{chunk_id}/requeues",
        "POST /chunks/{id}/requeues",
        on_status={409: "chunk is not escalated", 404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"requeued {chunk_id} — re-leasable at its current node")


@chunk_group.command("stop")
@click.argument("chunk_id")
@click.option("--by", "by", default="operator", help="Who is stopping (recorded on the fact).")
@_json_option
@_hub_url_options
def chunk_stop(chunk_id: str, by: str, as_json: bool, hub_url: str | None) -> None:
    """Terminally abandon CHUNK — the operator's last-resort verb (issue #118).

    A pure client of ``POST /api/chunks/{id}/stop``. The chunk derives ``stopped`` and
    never re-derives ``ready``; any live route is released in the same operation. 409
    when the chunk is already done/stopped. There is no ``un-stop``."""
    cli = CliContext.of(hub_url, as_json)
    resp = cli.post(
        f"/api/chunks/{chunk_id}/stop",
        "POST /chunks/{id}/stop",
        json_body={"by": by},
        on_status={409: "chunk is not stoppable", 404: f"no such chunk {chunk_id}"},
    )
    cli.finish(resp, f"stopped {chunk_id} — terminally abandoned, its route (if any) released")


@chunk_group.command("migrate")
@click.argument("chunk_id")
@click.option("--to-graph", default=None, help="Migration target — a graph id or name. Required unless --cancel.")
@click.option(
    "--node",
    default=None,
    help="Force landing on this node name on the target graph (forced mode). Omit for auto (name-matched).",
)
@click.option("--cancel", is_flag=True, default=False, help="Clear the chunk's standing migration intent.")
@_json_option
@_hub_url_options
def chunk_migrate(
    chunk_id: str,
    to_graph: str | None,
    node: str | None,
    cancel: bool,
    as_json: bool,
    hub_url: str | None,
) -> None:
    """Set, overwrite, or clear CHUNK's standing migration intent (issue #124).

    ``--node`` present selects ``forced``, absent selects ``auto``; ``--cancel`` clears
    a standing intent and conflicts with ``--to-graph``/``--node``. The intent is
    consulted at the chunk's next transition, never applied eagerly."""
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

    cli = CliContext.of(hub_url, as_json)
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


@chunk_group.command("group")
@click.argument("chunk_id")
@click.argument("merge_ids", nargs=-1, required=True)
@_json_option
@_hub_url_options
def chunk_group_cmd(chunk_id: str, merge_ids: tuple[str, ...], as_json: bool, hub_url: str | None) -> None:
    """Merge MERGE_IDS into CHUNK_ID, the survivor.

    A pure client of ``POST /api/chunks/{id}/group``: the survivor and every merge id
    must currently be **unacquired** — ``not_ready`` or ``ready``, in any mix (409
    otherwise). The survivor absorbs the union of work refs and keeps its own status."""
    cli = CliContext.of(hub_url, as_json)
    resp = cli.post(
        f"/api/chunks/{chunk_id}/group",
        "POST /chunks/{id}/group",
        json_body={"merge_chunk_ids": list(merge_ids)},
        on_status={404: f"unknown chunk {chunk_id}", 409: "one of the named chunks is not unacquired"},
    )
    body = resp.json()
    merged = ", ".join(body.get("merged_chunk_ids", [])) or "none"
    cli.show_lines(body, f"grouped into {body['chunk_id']} (merged: {merged})")


@chunk_group.command("work-items")
@click.argument("chunk_id")
@_json_option
@_hub_url_options
def chunk_work_items(chunk_id: str, as_json: bool, hub_url: str | None) -> None:
    """CHUNK's work items, pass-through — one entry per work ref, vendor-native.

    A pure client of ``GET /api/chunks/{id}/work-items``; a per-pointer forge failure
    degrades to that entry's own ``error`` rather than failing the whole read."""
    cli = CliContext.of(hub_url, as_json)
    resp = cli.get(
        f"/api/chunks/{chunk_id}/work-items",
        "GET /chunks/{id}/work-items",
        on_status={404: f"unknown chunk {chunk_id}"},
    )
    body = resp.json()
    cli.show(body, WorkItemListing(body.get("items", [])))


@chunk_group.command("pm", hidden=True)
@click.argument("chunk_id")
@_json_option
@_hub_url_options
@click.pass_context
def chunk_pm(ctx: click.Context, chunk_id: str, as_json: bool, hub_url: str | None) -> None:
    """Deprecated alias for ``blizzard hub chunk work-items`` (issue #55)."""
    click.echo(
        "warning: `blizzard hub chunk pm` is deprecated — use `blizzard hub chunk work-items`",
        err=True,
    )
    ctx.invoke(chunk_work_items, chunk_id=chunk_id, as_json=as_json, hub_url=hub_url)


# `blizzard hub runner` — issue #104 (issue #86a: enroll)


@hub.group("runner")
def runner_group() -> None:
    """Operator verbs over one runner: identity, liveness, and its pause brake."""


@runner_group.command("list")
@_json_option
@_hub_url_options
def runner_list(as_json: bool, hub_url: str | None) -> None:
    """The fleet registry — every runner with derived liveness + paused state."""
    cli = CliContext.of(hub_url, as_json)
    body = cli.get("/api/runners", "GET /runners").json()
    cli.show(body, RunnerListing(body.get("runners", [])))


@runner_group.command("show")
@click.argument("runner_id")
@_json_option
@_hub_url_options
def runner_show(runner_id: str, as_json: bool, hub_url: str | None) -> None:
    """One runner's derived liveness + paused state, symmetric with ``runner list``."""
    cli = CliContext.of(hub_url, as_json)
    resp = cli.get(f"/api/runners/{runner_id}", "GET /runners/{id}", on_status={404: f"unknown runner {runner_id}"})
    body = resp.json()
    cli.show(body, RunnerDetail(body))


@runner_group.command("pause")
@click.argument("runner_id")
@click.option("--by", "by", default="operator", help="Who is pausing (recorded on the fact).")
@_json_option
@_hub_url_options
def runner_pause(runner_id: str, by: str, as_json: bool, hub_url: str | None) -> None:
    """Pause a runner — it stops claiming new work; in-flight chunks run on."""
    _set_runner_pause(runner_id, verb="pause", by=by, hub_url=hub_url, as_json=as_json)


@runner_group.command("resume")
@click.argument("runner_id")
@click.option("--by", "by", default="operator", help="Who is resuming (recorded on the fact).")
@_json_option
@_hub_url_options
def runner_resume(runner_id: str, by: str, as_json: bool, hub_url: str | None) -> None:
    """Resume a paused runner — it claims work again on its next pull."""
    _set_runner_pause(runner_id, verb="resume", by=by, hub_url=hub_url, as_json=as_json)


def _set_runner_pause(runner_id: str, *, verb: str, by: str, hub_url: str | None, as_json: bool) -> None:
    cli = CliContext.of(hub_url, as_json)
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


@runner_group.command("enroll")
@click.argument("runner_id")
@_json_option
@_hub_url_options
def runner_enroll(runner_id: str, as_json: bool, hub_url: str | None) -> None:
    """Mint (or rotate) RUNNER_ID's bearer token; prints the plaintext exactly once.

    A thin client of ``POST /runners/{id}/enrollments`` (issue #86a). Re-running
    rotates: the old token stops resolving immediately. RUNNER_ID must already be
    registered at the hub (404 otherwise)."""
    cli = CliContext.of(hub_url, as_json)
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


@graph_group.command("list")
@_json_option
@_hub_url_options
def graph_list(as_json: bool, hub_url: str | None) -> None:
    """List every minted graph, newest first — name, graph_id, effective, retired."""
    cli = CliContext.of(hub_url, as_json)
    rows = cli.get("/api/graphs", "GET /graphs").json()
    cli.show(rows, GraphListing(rows))


@graph_group.command("show")
@click.argument("graph_id")
@_json_option
@_hub_url_options
def graph_show(graph_id: str, as_json: bool, hub_url: str | None) -> None:
    """One graph's full reified definition — nodes and edges."""
    cli = CliContext.of(hub_url, as_json)
    resp = cli.get(f"/api/graphs/{graph_id}", "GET /graphs/{id}", on_status={404: f"unknown graph {graph_id}"})
    body = resp.json()
    cli.show(body, GraphDetail(body))


@graph_group.command("mint")
@click.argument("path")
@_json_option
@_hub_url_options
def graph_mint(path: str, as_json: bool, hub_url: str | None) -> None:
    """Mint a graph from PATH's YAML definition; PATH may be ``-`` to read stdin.

    A file PATH inlines ``prompt``/``prompt_addendum`` file references relative to its
    own directory first (issue #123); stdin carries no such directory, so its YAML
    posts verbatim. Renders the full validation report on a 422 (issue #104)."""
    cli = CliContext.of(hub_url, as_json)
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


@graph_group.command("sync")
@_json_option
@_hub_url_options
def graph_sync(as_json: bool, hub_url: str | None) -> None:
    """Reconcile the hub's packaged graphs into its store, minting only what changed.

    The deploy verb (issue #146) — graphs live in the store, not on disk, so run it at
    the end of every deploy; it is idempotent. The **hub's own** packaged set is what is
    reconciled, not this CLI's. Exits non-zero only if a packaged graph failed to load."""
    cli = CliContext.of(hub_url, as_json)
    resp = cli.post("/api/graphs/sync", "POST /graphs/sync", json_body={})
    body = resp.json()
    cli.show(body, GraphSyncListing(body.get("entries", [])))
    if not body.get("ok", True):
        raise click.ClickException("one or more packaged graphs failed to reconcile")


@graph_group.command("retire")
@click.argument("graph_id")
@click.option("--by", "by", default="operator", help="Who is retiring (recorded on the fact).")
@_json_option
@_hub_url_options
def graph_retire(graph_id: str, by: str, as_json: bool, hub_url: str | None) -> None:
    """Retire GRAPH_ID — excludes it from name resolution; in-flight chunks run on."""
    _set_graph_lifecycle(graph_id, verb="retire", by=by, hub_url=hub_url, as_json=as_json)


@graph_group.command("enable")
@click.argument("graph_id")
@click.option("--by", "by", default="operator", help="Who is re-enabling (recorded on the fact).")
@_json_option
@_hub_url_options
def graph_enable(graph_id: str, by: str, as_json: bool, hub_url: str | None) -> None:
    """Re-enable a retired GRAPH_ID — restores normal newest-per-name derivation."""
    _set_graph_lifecycle(graph_id, verb="enable", by=by, hub_url=hub_url, as_json=as_json)


@graph_group.command("follow-latest")
@click.argument("graph_id")
@click.argument("value", type=click.Choice(["true", "false", "inherit"]))
@click.option("--by", "by", default="operator", help="Who is setting the policy (recorded on the fact).")
@_json_option
@_hub_url_options
def graph_follow_latest(graph_id: str, value: str, by: str, as_json: bool, hub_url: str | None) -> None:
    """Set GRAPH_ID's follow-latest policy: true, false, or inherit (issue #164).

    With the policy on, a chunk pinned to this mint re-pins to the newest enabled mint
    of the same *name* at its next transition. ``inherit`` (the stored ``null``, and
    every mint's default) defers to the hub's own ``follow_latest``."""
    cli = CliContext.of(hub_url, as_json)
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


def _set_graph_lifecycle(graph_id: str, *, verb: str, by: str, hub_url: str | None, as_json: bool) -> None:
    cli = CliContext.of(hub_url, as_json)
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


@queue_group.command("show")
@_json_option
@_hub_url_options
def queue_show(as_json: bool, hub_url: str | None) -> None:
    """The hub-ordered ready queue, read-only — a client of ``GET /api/queue``."""
    cli = CliContext.of(hub_url, as_json)
    body = cli.get("/api/queue", "GET /queue").json()
    cli.show(body, QueueListing(body.get("entries", [])))


@queue_group.command("set")
@click.argument("chunk_ids", nargs=-1, required=True)
@_json_option
@_hub_url_options
def queue_set(chunk_ids: tuple[str, ...], as_json: bool, hub_url: str | None) -> None:
    """Replace the whole ready-queue order with CHUNK_IDS, front to back.

    A pure client of ``PUT /api/queue`` — an idempotent whole-order replacement
    (issue #104). Every id must be a ready chunk (409) and must not repeat (422); a
    ready chunk not named keeps its relative order, appended after the named ones."""
    cli = CliContext.of(hub_url, as_json)
    resp = cli.put(
        "/api/queue",
        "PUT /queue",
        json_body={"chunk_ids": list(chunk_ids)},
        on_status={409: "one of the named chunks is not a ready chunk", 422: "chunk_ids must not repeat"},
    )
    body = resp.json()
    cli.show_lines(body, f"queue order set ({len(body.get('entries', []))} ready chunk(s))")


@queue_group.command("move")
@click.argument("chunk_id")
@click.argument("position", type=int)
@_json_option
@_hub_url_options
def queue_move(chunk_id: str, position: int, as_json: bool, hub_url: str | None) -> None:
    """Move CHUNK_ID to POSITION in the ready queue (``0`` is the front).

    A client of the single-chunk fractional ``POST /api/queue/position`` (issue #137):
    reads the current order, drops CHUNK_ID out of it, clamps POSITION into what's left,
    and sends one anchor. 409 when CHUNK_ID is not a ready chunk."""
    cli = CliContext.of(hub_url, as_json)
    peek = cli.get("/api/queue", "GET /queue")
    rest = [entry["chunk_id"] for entry in peek.json().get("entries", []) if entry["chunk_id"] != chunk_id]
    index = min(max(position, 0), len(rest))
    after_chunk_id = rest[index - 1] if index > 0 else None
    resp = cli.post(
        "/api/queue/position",
        "POST /queue/position",
        json_body={"chunk_id": chunk_id, "after_chunk_id": after_chunk_id},
        on_status={409: f"chunk {chunk_id} is not a ready chunk"},
    )
    cli.finish(resp, f"moved {chunk_id} to position {position}")


# `blizzard hub decision` — issue #104


@hub.group("decision")
def decision_group() -> None:
    """Operator verbs over open gate decisions: list, resolve."""


@decision_group.command("list")
@_json_option
@_hub_url_options
def decision_list(as_json: bool, hub_url: str | None) -> None:
    """List open decisions awaiting a human (gate surfacing)."""
    cli = CliContext.of(hub_url, as_json)
    body = cli.get("/api/decisions", "GET /decisions").json()
    cli.show(body, DecisionListing(body.get("decisions", [])))


@decision_group.command("resolve")
@click.argument("decision_id")
@click.argument("choice")
@click.option("--by", "resolved_by", default="operator", help="Who is resolving (recorded on the resolution).")
@_json_option
@_hub_url_options
def decision_resolve(decision_id: str, choice: str, resolved_by: str, as_json: bool, hub_url: str | None) -> None:
    """Resolve an open decision by picking CHOICE (first-write-wins).

    A pure client of ``POST /api/decisions/{id}/resolutions`` (issue #104's pluralized
    resolution route)."""
    cli = CliContext.of(hub_url, as_json)
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


@question_group.command("list")
@_json_option
@_hub_url_options
def question_list(as_json: bool, hub_url: str | None) -> None:
    """Every open (unanswered) question across the fleet."""
    cli = CliContext.of(hub_url, as_json)
    rows = cli.get("/api/questions", "GET /questions").json()
    cli.show(rows, QuestionListing(rows))


@question_group.command("answer")
@click.argument("question_id")
@click.argument("answer_text")
@click.option("--by", "answered_by", default="operator", help="Who is answering (recorded on the row).")
@_json_option
@_hub_url_options
def question_answer(question_id: str, answer_text: str, answered_by: str, as_json: bool, hub_url: str | None) -> None:
    """Answer an open question (first-write-wins CAS at the hub).

    A racing second answer loses and is told who already answered. A pure client of
    ``POST /api/questions/{id}/answers`` (issue #104)."""
    cli = CliContext.of(hub_url, as_json)
    resp = cli.send(
        "post", f"/api/questions/{question_id}/answers", json_body={"answer": answer_text, "answered_by": answered_by}
    )
    if resp.status_code == httpx.codes.CONFLICT:
        winner = resp.json()
        raise click.ClickException(f"already answered by {winner.get('answered_by')}: {winner.get('answer')!r}")
    cli.check(resp, "POST /questions/{id}/answers", on_status={404: f"unknown question {question_id}"})
    cli.finish(resp, f"answered {question_id}: {answer_text!r} (the runner will resume the session)")
