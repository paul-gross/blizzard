"""``blizzard hub <cmd>`` — the fleet surface (design/cli.md).

Client verbs are pure clients of the hub's HTTP API; ``host`` *becomes* the hub
daemon (D-061). Only ``init`` / ``migrate`` / ``host`` are implemented in the
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

from blizzard.foundation.store.migrations import RevisionMismatchError
from blizzard.hub.app import build_hosted_app
from blizzard.hub.config import ConfigError, HubConfig
from blizzard.hub.runtime import ensure_current_revision, init_environment, migrate, migration_runner

# The hub the client verbs talk to (design/cli.md): ``BZ_HUB_URL`` overrides the
# colocated default (band +2). Client verbs are pure API clients (D-023/D-061).
ENV_HUB_URL = "BZ_HUB_URL"
DEFAULT_HUB_URL = "http://127.0.0.1:8421"
_CLIENT_TIMEOUT = 15.0


def _stub(verb: str) -> None:
    raise click.ClickException(f"`blizzard hub {verb}` is not yet implemented (scaffold stub).")


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
@click.argument("directory", default=".")
def init(directory: str) -> None:
    """Scaffold config + data dir + a migrated store under DIRECTORY. Idempotent."""
    config = init_environment(Path(directory))
    revision = migration_runner(config).current_revision()
    click.echo(f"hub runtime ready at {config.root} (store revision {revision})")


@hub.command("migrate")
@click.option("--dir", "directory", default=".", help="Hub runtime directory.")
@click.option("--down", default=None, help="Reverse migrations down to this revision (e.g. base).")
def migrate_cmd(directory: str, down: str | None) -> None:
    """Apply pending store migrations, or reverse with --down <rev>."""
    try:
        migrate(Path(directory), down=down)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("migrated" if down is None else f"reversed to {down}")


@hub.command()
@click.option("--dir", "directory", default=".", help="Hub runtime directory.")
@click.option("--host", "host_", default=None, help="Bind host (overrides config).")
@click.option("--port", type=int, default=None, help="Bind port (overrides config).")
def host(directory: str, host_: str | None, port: int | None) -> None:
    """Become the blizzard-hub daemon: HTTP API + SSE + the embedded web app."""
    try:
        config = HubConfig.load(Path(directory), host=host_, port=port)
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    try:
        ensure_current_revision(config)
    except RevisionMismatchError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"serving blizzard-hub on {config.host}:{config.port}")
    uvicorn.run(build_hosted_app(config), host=config.host, port=config.port)


@hub.command()
@click.option("--url", "url", default=None, help="Hub base URL (overrides $BZ_HUB_URL).")
def status(url: str | None) -> None:
    """The fleet view: every chunk with its derived status, the runners, and open questions.

    A pure client of the hub API (design/cli.md): ``GET /chunks`` + ``GET /runners`` +
    ``GET /questions``, the same facts the board renders, in the terminal (D-004)."""
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
        brake = " [paused]" if r.get("paused") else ""
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

    Writes the answer where the question row lives ([ask-answer.md]); the runner picks
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
    """List open decisions awaiting a human (gate surfacing, D-052)."""
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
    """Resolve an open decision by picking CHOICE (first-write-wins, D-045)."""
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


@hub.command()
def ingest() -> None:
    """Ingest PM items by pointer, minting chunks."""
    _stub("ingest")


@hub.command()
@click.argument("chunk_id")
@click.option("--hub-url", default=None, help=f"Hub API base URL (default ${ENV_HUB_URL} or {DEFAULT_HUB_URL}).")
def requeue(chunk_id: str, hub_url: str | None) -> None:
    """Close an escalation by supersession: requeue CHUNK at its current node (D-067)."""
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
def detach(chunk_id: str) -> None:
    """Forcibly release a chunk from its runner (D-088)."""
    _stub("detach")


@hub.command()
@click.argument("runner_id")
@click.option("--by", "by", default="operator", help="Who is pausing (recorded on the fact).")
@click.option("--hub-url", default=None, help=f"Hub API base URL (default ${ENV_HUB_URL} or {DEFAULT_HUB_URL}).")
def pause(runner_id: str, by: str, hub_url: str | None) -> None:
    """Pause a runner — it stops claiming new work; in-flight chunks run on (D-043)."""
    _set_runner_pause(runner_id, verb="pause", by=by, hub_url=hub_url)


@hub.command()
@click.argument("runner_id")
@click.option("--by", "by", default="operator", help="Who is resuming (recorded on the fact).")
@click.option("--hub-url", default=None, help=f"Hub API base URL (default ${ENV_HUB_URL} or {DEFAULT_HUB_URL}).")
def resume(runner_id: str, by: str, hub_url: str | None) -> None:
    """Resume a paused runner — it claims work again on its next pull (D-043)."""
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
    state = "paused" if body.get("paused") else "running"
    click.echo(f"runner {runner_id} is now {state}")
