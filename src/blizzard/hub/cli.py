"""``blizzard hub <cmd>`` — the fleet surface.

Client verbs are pure clients of the hub's HTTP API; ``host`` *becomes* the hub
daemon. Only ``init`` / ``migrate`` / ``host`` are implemented in the
scaffold — the rest are stubs that name themselves, present in ``--help`` and
filled in by the backend builder. This module is CLI top-level glue, so ``echo``
for user output is fine here (``bzh:structlog-logging``); diagnostics go through
structlog inside the runtime and app.
"""

from __future__ import annotations

import os
from pathlib import Path

import click
import httpx
import uvicorn

from blizzard.cli.host_directory import resolve_host_directory
from blizzard.foundation.store.migrations import RevisionMismatchError
from blizzard.hub.app import build_hosted_app
from blizzard.hub.config import ConfigError, HubConfig
from blizzard.hub.runtime import ensure_current_revision, init_environment, migrate, migration_runner

# The hub the client verbs talk to: ``BZ_HUB_URL`` overrides the
# colocated default (band +2). Client verbs are pure API clients.
ENV_HUB_URL = "BZ_HUB_URL"
DEFAULT_HUB_URL = "http://127.0.0.1:8421"
_CLIENT_TIMEOUT = 15.0

# The runtime root the dir-taking verbs resolve, highest to lowest: an explicit
# ``--dir`` (or ``init``'s DIRECTORY), then ``BZ_HUB_DIR``, then the cwd. The env rung
# is what lets winter's per-env band (`[env.<name>.vars]`) aim one feature env at a
# chosen runtime root — a store snapshot, or a shared dir during an exclusive handoff —
# without a bespoke command line per invocation (issue #39). Selectable, not shareable:
# the store is still single-writer, so two live daemons on one `hub.db` remains unsafe.
ENV_HUB_DIR = "BZ_HUB_DIR"
DEFAULT_DIR = "."


def _hub_url(override: str | None) -> str:
    return override or os.environ.get(ENV_HUB_URL, DEFAULT_HUB_URL)


def _api_error(operation: str, exc: Exception) -> click.ClickException:
    return click.ClickException(f"{operation} failed: {exc}")


@click.group(invoke_without_command=True)
@click.pass_context
def hub(ctx: click.Context) -> None:
    """Talk to — or become — the blizzard hub."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(host)


@hub.command()
@click.argument("directory", default=DEFAULT_DIR, envvar=ENV_HUB_DIR)
def init(directory: str) -> None:
    """Scaffold config + data dir + a migrated store under DIRECTORY. Idempotent.

    DIRECTORY defaults to $BZ_HUB_DIR, then the cwd."""
    config = init_environment(Path(directory))
    revision = migration_runner(config).current_revision()
    click.echo(f"hub runtime ready at {config.root} (store revision {revision})")


@hub.command("migrate")
@click.option(
    "--dir", "directory", default=DEFAULT_DIR, envvar=ENV_HUB_DIR, help="Hub runtime directory (overrides $BZ_HUB_DIR)."
)
@click.option("--down", default=None, help="Reverse migrations down to this revision (e.g. base).")
def migrate_cmd(directory: str, down: str | None) -> None:
    """Apply pending store migrations, or reverse with --down <rev>."""
    try:
        migrate(Path(directory), down=down)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("migrated" if down is None else f"reversed to {down}")


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
def host(directory: str | None, dir_option: str, host_: str | None, port: int | None) -> None:
    """Become the blizzard-hub daemon: HTTP API + SSE + the embedded web app.

    DIRECTORY (positional) and --dir are equivalent — pass one; giving both requires
    they agree. Defaults to $BZ_HUB_DIR, then the cwd."""
    directory = resolve_host_directory(directory, dir_option)
    try:
        config = HubConfig.load(Path(directory), host=host_, port=port)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        ensure_current_revision(config)
    except RevisionMismatchError as exc:
        raise click.ClickException(str(exc)) from exc
    # Composition can still reject the config (an ``[[pm_source]]`` naming an unset
    # ``token_env`` fails here, at boot, by design). Surface it as the same
    # clean CLI error the config-load and migration guards above raise, not a
    # traceback; and build before announcing, so we never claim to serve and then die.
    try:
        app = build_hosted_app(config)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"serving blizzard-hub on {config.host}:{config.port}")
    uvicorn.run(app, host=config.host, port=config.port)


@hub.command()
@click.option("--url", "url", default=None, help="Hub base URL (overrides $BZ_HUB_URL).")
def status(url: str | None) -> None:
    """The fleet view: every chunk with its derived status, the runners, and open questions.

    A pure client of the hub API: ``GET /chunks`` + ``GET /runners`` +
    ``GET /questions``, the same facts the board renders, in the terminal."""
    base = _hub_url(url)
    try:
        with httpx.Client(base_url=base, timeout=_CLIENT_TIMEOUT) as client:
            chunks = client.get("/api/chunks")
            chunks.raise_for_status()
            runners = client.get("/api/runners")
            runners.raise_for_status()
            questions = client.get("/api/questions")
            questions.raise_for_status()
    except httpx.HTTPError as exc:
        raise click.ClickException(f"hub status: could not reach the hub at {base} ({exc})") from exc

    rows = chunks.json()
    click.echo(f"chunks ({len(rows)}):")
    for chunk in rows:
        node = chunk.get("current_node_id") or "-"
        click.echo(f"  {chunk['chunk_id']}  {chunk['status']:<16} @ {node}")
    fleet = runners.json().get("runners", [])
    click.echo(f"\nrunners ({len(fleet)}):")
    for r in fleet:
        liveness = "online" if r.get("online") else "offline"
        # Name which brake is on (issue #43): "paused" alone would hide whether the fleet
        # stopped this runner or it stopped itself — and they are cleared by different verbs.
        brakes = [name for name, on in (("hub", r.get("hub_paused")), ("local", r.get("locally_paused"))) if on]
        brake = f" [paused: {'+'.join(brakes)}]" if brakes else ""
        click.echo(f"  {r['runner_id']:<16} {liveness:<8} ws={r.get('workspace_id', '-')}{brake}")
    open_qs = questions.json()
    click.echo(f"\nopen questions ({len(open_qs)}):")
    for q in open_qs:
        opts = f"  [{'|'.join(q.get('options') or [])}]" if q.get("options") else ""
        click.echo(f"  {q['question_id']}  (chunk {q['chunk_id']}): {q['question']}{opts}")


@hub.command()
@click.argument("question_id")
@click.argument("answer_text")
@click.option("--by", "answered_by", default="operator", help="Who is answering (recorded on the row).")
@click.option("--url", "url", default=None, help="Hub base URL (overrides $BZ_HUB_URL).")
def answer(question_id: str, answer_text: str, answered_by: str, url: str | None) -> None:
    """Answer an open question (first-write-wins CAS at the hub).

    Writes the answer where the question row lives; the runner picks
    it up and resumes the dormant session. A racing second answer loses and is told who
    already answered."""
    base = _hub_url(url)
    try:
        with httpx.Client(base_url=base, timeout=_CLIENT_TIMEOUT) as client:
            resp = client.post(
                f"/api/questions/{question_id}/answer",
                json={"answer": answer_text, "answered_by": answered_by},
            )
    except httpx.HTTPError as exc:
        raise click.ClickException(f"hub answer: could not reach the hub at {base} ({exc})") from exc

    if resp.status_code == httpx.codes.CONFLICT:
        winner = resp.json()
        raise click.ClickException(f"already answered by {winner.get('answered_by')}: {winner.get('answer')!r}")
    if resp.status_code == httpx.codes.NOT_FOUND:
        raise click.ClickException(f"unknown question {question_id}")
    try:
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise click.ClickException(f"hub answer: {exc}") from exc
    click.echo(f"answered {question_id}: {answer_text!r} (the runner will resume the session)")


@hub.command()
@click.option("--hub-url", default=None, help=f"Hub API base URL (default ${ENV_HUB_URL} or {DEFAULT_HUB_URL}).")
def decisions(hub_url: str | None) -> None:
    """List open decisions awaiting a human (gate surfacing)."""
    try:
        resp = httpx.get(f"{_hub_url(hub_url).rstrip('/')}/api/decisions", timeout=_CLIENT_TIMEOUT)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise _api_error("GET /decisions", exc) from exc
    rows = resp.json().get("decisions", [])
    if not rows:
        click.echo("no open decisions")
        return
    for d in rows:
        choices = ", ".join(c["name"] for c in d.get("choices", []))
        click.echo(f"{d['decision_id']}  chunk={d['chunk_id']}  node={d['node_name']}  choices=[{choices}]")


@hub.command()
@click.argument("decision_id")
@click.argument("choice")
@click.option("--by", "resolved_by", default="operator", help="Who is resolving (recorded on the resolution).")
@click.option("--hub-url", default=None, help=f"Hub API base URL (default ${ENV_HUB_URL} or {DEFAULT_HUB_URL}).")
def decide(decision_id: str, choice: str, resolved_by: str, hub_url: str | None) -> None:
    """Resolve an open decision by picking CHOICE (first-write-wins)."""
    url = f"{_hub_url(hub_url).rstrip('/')}/api/decisions/{decision_id}/resolution"
    try:
        resp = httpx.post(url, json={"choice": choice, "resolved_by": resolved_by}, timeout=_CLIENT_TIMEOUT)
    except httpx.HTTPError as exc:
        raise _api_error("POST /decisions/{id}/resolution", exc) from exc
    if resp.status_code == httpx.codes.CONFLICT:
        body = resp.json()
        raise click.ClickException(f"already resolved by {body.get('already_resolved_by')}")
    if resp.status_code == httpx.codes.NOT_FOUND:
        raise click.ClickException(f"no such decision {decision_id}")
    if resp.status_code == httpx.codes.BAD_REQUEST:
        raise click.ClickException(resp.json().get("detail", "invalid choice"))
    try:
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise _api_error("POST /decisions/{id}/resolution", exc) from exc
    body = resp.json()
    click.echo(f"decision {decision_id} resolved: {body['choice']} (by {body['resolved_by']})")


def _parse_pointer(token: str) -> str:
    """The ingest token the CLI hands the hub.

    The CLI carries no grammar of its own any more: the hub resolves every token
    against its configured PM sources' own ``parse`` (``{name}:{ref}``,
    ``{name}#{ref}``, or the item's own URL), so a token travels
    through verbatim. The one thing that survives here is the deprecated
    ``github:<rest>`` prefix: it warns on stderr and passes ``rest`` on its own
    merits rather than silently accepting a provider tag the pointer no longer
    carries."""
    if token.startswith("github:"):
        rest = token[len("github:") :]
        click.echo(
            f"warning: the 'github:' pointer prefix is deprecated (in {token!r}) — resolving {rest!r} on its own",
            err=True,
        )
        return rest
    return token


@hub.command()
@click.argument("pointers", nargs=-1, required=True)
@click.option("--hub-url", default=None, help=f"Hub API base URL (default ${ENV_HUB_URL} or {DEFAULT_HUB_URL}).")
def ingest(pointers: tuple[str, ...], hub_url: str | None) -> None:
    """Ingest PM items by token, minting a chunk.

    Each POINTER is a source-native token — ``source:ref`` (e.g. ``blizzard:26``),
    ``source#ref``, or a pasted PM item URL; pass one or more — a batch mints one
    chunk carrying every pointer. A pure client of the hub API: ``POST /api/chunks``.
    The hub resolves each token against its configured PM sources and 422s one none
    of them claims, naming the token and what is configured; 409 when a resolved
    pointer is already held by a live chunk."""
    tokens = [_parse_pointer(p) for p in pointers]
    url = f"{_hub_url(hub_url).rstrip('/')}/api/chunks"
    try:
        resp = httpx.post(url, json={"tokens": tokens}, timeout=_CLIENT_TIMEOUT)
    except httpx.HTTPError as exc:
        raise _api_error("POST /chunks", exc) from exc
    if resp.status_code == httpx.codes.CONFLICT:
        body = resp.json()
        raise click.ClickException(
            f"pointer {body.get('source')}#{body.get('ref')} already held by chunk {body.get('existing_chunk_id')}"
        )
    if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        raise click.ClickException(resp.json().get("detail", "at least one token required"))
    try:
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise _api_error("POST /chunks", exc) from exc
    chunk_id = resp.json()["chunk_id"]
    click.echo(f"ingested {len(tokens)} pointer(s) → chunk {chunk_id}")


@hub.command()
@click.argument("chunk_id")
@click.option("--hub-url", default=None, help=f"Hub API base URL (default ${ENV_HUB_URL} or {DEFAULT_HUB_URL}).")
def promote(chunk_id: str, hub_url: str | None) -> None:
    """Promote a not-ready CHUNK to ready so a runner may claim it.

    A pure client of the hub API: ``POST /api/chunks/{id}/promote``. Idempotent — promoting
    an already-ready chunk is a harmless no-op; 404 only when the chunk is unknown."""
    url = f"{_hub_url(hub_url).rstrip('/')}/api/chunks/{chunk_id}/promote"
    try:
        resp = httpx.post(url, timeout=_CLIENT_TIMEOUT)
    except httpx.HTTPError as exc:
        raise _api_error("POST /chunks/{id}/promote", exc) from exc
    if resp.status_code == httpx.codes.NOT_FOUND:
        raise click.ClickException(f"no such chunk {chunk_id}")
    try:
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise _api_error("POST /chunks/{id}/promote", exc) from exc
    click.echo(f"promoted {chunk_id} — now ready for a runner to claim")


@hub.command()
@click.argument("chunk_id")
@click.option("--hub-url", default=None, help=f"Hub API base URL (default ${ENV_HUB_URL} or {DEFAULT_HUB_URL}).")
def requeue(chunk_id: str, hub_url: str | None) -> None:
    """Close an escalation by supersession: requeue CHUNK at its current node."""
    url = f"{_hub_url(hub_url).rstrip('/')}/api/chunks/{chunk_id}/requeues"
    try:
        resp = httpx.post(url, timeout=_CLIENT_TIMEOUT)
    except httpx.HTTPError as exc:
        raise _api_error("POST /chunks/{id}/requeues", exc) from exc
    if resp.status_code == httpx.codes.CONFLICT:
        raise click.ClickException(resp.json().get("detail", "chunk is not escalated"))
    if resp.status_code == httpx.codes.NOT_FOUND:
        raise click.ClickException(f"no such chunk {chunk_id}")
    try:
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise _api_error("POST /chunks/{id}/requeues", exc) from exc
    click.echo(f"requeued {chunk_id} — re-leasable at its current node")


@hub.command()
@click.argument("chunk_id")
@click.option("--hub-url", default=None, help=f"Hub API base URL (default ${ENV_HUB_URL} or {DEFAULT_HUB_URL}).")
def detach(chunk_id: str, hub_url: str | None) -> None:
    """Forcibly release CHUNK from its runner.

    A pure client of the hub API: ``POST /api/chunks/{id}/detach``. The chunk re-derives
    ready and is re-claimable at its current node; the holding runner releases it on its
    next tick. 409 when the chunk has no live route to release."""
    url = f"{_hub_url(hub_url).rstrip('/')}/api/chunks/{chunk_id}/detach"
    try:
        resp = httpx.post(url, timeout=_CLIENT_TIMEOUT)
    except httpx.HTTPError as exc:
        raise _api_error("POST /chunks/{id}/detach", exc) from exc
    if resp.status_code == httpx.codes.CONFLICT:
        raise click.ClickException(resp.json().get("detail", "chunk has no live route"))
    if resp.status_code == httpx.codes.NOT_FOUND:
        raise click.ClickException(f"no such chunk {chunk_id}")
    try:
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise _api_error("POST /chunks/{id}/detach", exc) from exc
    click.echo(f"detached {chunk_id} — released from its runner, re-claimable at its current node")


@hub.command("pause-chunk")
@click.argument("chunk_id")
@click.option("--by", "by", default="operator", help="Who is pausing (recorded on the fact).")
@click.option("--hub-url", default=None, help=f"Hub API base URL (default ${ENV_HUB_URL} or {DEFAULT_HUB_URL}).")
def pause_chunk(chunk_id: str, by: str, hub_url: str | None) -> None:
    """Pause CHUNK — the runner kills and parks the worker but keeps the claim (issue #46).

    A pure client of the hub API: ``POST /api/chunks/{id}/pause``. Unlike ``detach``, no
    route is released and no retry is consumed. 409 when the chunk is done/stopped/
    delivering."""
    url = f"{_hub_url(hub_url).rstrip('/')}/api/chunks/{chunk_id}/pause"
    try:
        resp = httpx.post(url, json={"by": by}, timeout=_CLIENT_TIMEOUT)
    except httpx.HTTPError as exc:
        raise _api_error("POST /chunks/{id}/pause", exc) from exc
    if resp.status_code == httpx.codes.CONFLICT:
        raise click.ClickException(resp.json().get("detail", "chunk is not pausable"))
    if resp.status_code == httpx.codes.NOT_FOUND:
        raise click.ClickException(f"no such chunk {chunk_id}")
    try:
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise _api_error("POST /chunks/{id}/pause", exc) from exc
    click.echo(f"paused {chunk_id} — its worker will be killed and parked, keeping the claim")


@hub.command("resume-chunk")
@click.argument("chunk_id")
@click.option("--by", "by", default="operator", help="Who is resuming (recorded on the fact).")
@click.option("--hub-url", default=None, help=f"Hub API base URL (default ${ENV_HUB_URL} or {DEFAULT_HUB_URL}).")
def resume_chunk(chunk_id: str, by: str, hub_url: str | None) -> None:
    """Resume a paused CHUNK — the runner resumes the parked worker in place (issue #46).

    A pure client of the hub API: ``POST /api/chunks/{id}/resume``. Idempotent: resuming
    an unpaused chunk is a harmless no-op. 404 only when the chunk is unknown."""
    url = f"{_hub_url(hub_url).rstrip('/')}/api/chunks/{chunk_id}/resume"
    try:
        resp = httpx.post(url, json={"by": by}, timeout=_CLIENT_TIMEOUT)
    except httpx.HTTPError as exc:
        raise _api_error("POST /chunks/{id}/resume", exc) from exc
    if resp.status_code == httpx.codes.NOT_FOUND:
        raise click.ClickException(f"no such chunk {chunk_id}")
    try:
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise _api_error("POST /chunks/{id}/resume", exc) from exc
    click.echo(f"resumed {chunk_id} — its worker resumes in place")


@hub.command()
@click.argument("runner_id")
@click.option("--by", "by", default="operator", help="Who is pausing (recorded on the fact).")
@click.option("--hub-url", default=None, help=f"Hub API base URL (default ${ENV_HUB_URL} or {DEFAULT_HUB_URL}).")
def pause(runner_id: str, by: str, hub_url: str | None) -> None:
    """Pause a runner — it stops claiming new work; in-flight chunks run on."""
    _set_runner_pause(runner_id, verb="pause", by=by, hub_url=hub_url)


@hub.command()
@click.argument("runner_id")
@click.option("--by", "by", default="operator", help="Who is resuming (recorded on the fact).")
@click.option("--hub-url", default=None, help=f"Hub API base URL (default ${ENV_HUB_URL} or {DEFAULT_HUB_URL}).")
def resume(runner_id: str, by: str, hub_url: str | None) -> None:
    """Resume a paused runner — it claims work again on its next pull."""
    _set_runner_pause(runner_id, verb="resume", by=by, hub_url=hub_url)


def _set_runner_pause(runner_id: str, *, verb: str, by: str, hub_url: str | None) -> None:
    url = f"{_hub_url(hub_url).rstrip('/')}/api/runners/{runner_id}/{verb}"
    try:
        resp = httpx.post(url, json={"by": by}, timeout=_CLIENT_TIMEOUT)
    except httpx.HTTPError as exc:
        raise _api_error(f"POST /runners/{{id}}/{verb}", exc) from exc
    if resp.status_code == httpx.codes.NOT_FOUND:
        raise click.ClickException(f"unknown runner {runner_id}")
    try:
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise _api_error(f"POST /runners/{{id}}/{verb}", exc) from exc
    body = resp.json()
    state = "paused" if body.get("hub_paused") else "running"
    click.echo(f"runner {runner_id} is now {state} (at the hub)")
    if body.get("locally_paused"):
        # Resuming here cannot clear the runner's own brake, so don't imply it did.
        click.echo(f"note: runner {runner_id} also paused itself — clear that with `blizzard runner start`")
