from __future__ import annotations

import json
from dataclasses import dataclass
from http import HTTPStatus
from typing import cast
from urllib.parse import quote

import click
from pydantic import TypeAdapter

from blizzard.cli.collaborators import HTTPStatusError
from blizzard.cli.get_group import GetGroup
from blizzard.foundation.artifacts import ArtifactKind, ArtifactScope
from blizzard.foundation.roles import dto
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.runner.cli.response import decode_answer
from blizzard.runner.cli.worker_call import WorkerCall
from blizzard.runner.lifecycle.artifacts import carries_content, read_only_reason
from blizzard.wire.attachments import AttachmentResponse, StagedAttachment
from blizzard.wire.envelope import WorkerArtifact
from blizzard.wire.git_commits import GitCommitDeclarationResponse


@click.group("artifact", cls=GetGroup)
def artifact_group() -> None:
    """Worker: read node-step, graph, and system artifacts; write this node-step's own.
    Every verb acts on this worker's own chunk — none names another. ``--scope`` picks node
    scope, the graph mint's baked-in declarations, or blizzard's published system-artifact set.
    ``create`` *stages* a submission, published on completion."""


@dto
@dataclass(frozen=True)
class ArtifactEntry:
    """One ``list``-view entry — every field but ``content``, which collapses to
    its ``bytes`` length (``None`` when the artifact carries none, i.e. ``git_commit``)."""

    artifact: dict[str, object]

    @property
    def summary(self) -> dict[str, object]:  # ast-grep-ignore: bzh:property-delegates
        content = self.artifact.get("content")
        summary = {k: v for k, v in self.artifact.items() if k != "content"}
        summary["bytes"] = len(content.encode("utf-8")) if isinstance(content, str) else None
        return summary


def _refuse_read_only_scope(verb: str, scope: str | None) -> None:
    """``create``/``commit``/``staged`` are node-scope only; refuses with the scope's own
    :func:`~blizzard.runner.lifecycle.artifacts.read_only_reason`
    (``blizzard-context:/standards/worker-nodes/declarations.md``)."""
    reason = read_only_reason(ArtifactScope(scope)) if scope else None
    if reason is not None:
        raise click.ClickException(f"artifact {verb}: {scope} scope is read-only — {reason}")


_SCOPE_CHOICE = click.Choice([s.value for s in ArtifactScope])


def _staged_for_scope(worker: WorkerCall, scope: str | None) -> list[dict[str, object]]:
    """This node-step's own staged (not-yet-published) submissions, or ``[]`` for a SCOPE that
    excludes node — ``graph`` and ``system`` never have one. Also ``[]``, noted on stderr, on a
    failed read."""
    if scope not in (None, ArtifactScope.NODE.value):
        return []
    try:
        resp = worker.get(worker.leased("attachments"), failure="could not read the staged artifacts")
    except click.ClickException as exc:
        click.echo(f"artifact list: {exc.message}; omitting staged entries", err=True)
        return []
    raw, _ = decode_answer(resp, TypeAdapter(list[StagedAttachment]), verb="artifact list", where="attachments")
    return cast("list[dict[str, object]]", raw)


@artifact_group.command("list")
@click.option(
    "--content",
    "content",
    is_flag=True,
    default=False,
    help="Include each artifact's full content instead of just its byte length.",
)
@click.option(
    "--scope",
    "scope",
    type=_SCOPE_CHOICE,
    default=None,
    help="Filter to one scope — `node` (this node-step's own artifacts), `graph` (the graph "
    "mint's baked-in declarations), or `system` (blizzard's own published documents). Omitted "
    "reads all three.",
)
def artifact_list(content: bool, scope: str | None) -> None:
    """Worker: list this node-step's artifacts as kind-discriminated JSON, resolved latest-by-epoch,
    plus the graph mint's own baked-in declarations and blizzard's published system-artifact
    set — ``--scope`` narrows to one. Content is elided by default; ``--content`` includes it.
    Also includes this node-step's own staged, not-yet-published submissions,
    each carrying ``"staged": true`` — everything published carries ``"staged": false``."""
    worker = WorkerCall.of("artifact list")
    resp = worker.get(
        worker.leased("artifacts"),
        failure="could not read the artifacts",
        params={"scope": scope} if scope else None,
    )
    raw, _ = decode_answer(resp, TypeAdapter(list[WorkerArtifact]), verb="artifact list", where="artifacts")
    published = cast("list[dict[str, object]]", raw)
    staged = _staged_for_scope(worker, scope)
    entries = [{**a, "staged": False} for a in published] + [{**a, "staged": True} for a in staged]
    if content:
        click.echo(json.dumps(entries))
        return
    click.echo(json.dumps([ArtifactEntry(e).summary for e in entries]))


def _staged_names(worker: WorkerCall) -> set[str] | None:
    """The names of this node-step's own staged submissions, or ``None`` on a failed read — a
    failed staged-check must never mask the original not-found error."""
    try:
        resp = worker.get(worker.leased("attachments"), failure="could not read the staged artifacts")
        _, staged = decode_answer(resp, TypeAdapter(list[StagedAttachment]), verb="artifact get", where="attachments")
    except click.ClickException:
        return None
    return {a.name for a in staged}


def _is_not_found(exc: click.ClickException) -> bool:
    """Only a genuine ``404`` is worth a second, staged-set lookup — any other rejection (a
    ``409`` ambiguity, a ``403``) already names its own cause and gets no staged detour."""
    cause = exc.__cause__
    if not isinstance(cause, HTTPStatusError):
        return False
    return getattr(cause.response, "status_code", None) == HTTPStatus.NOT_FOUND


@artifact_group.command("get")
@click.argument("name_arg", metavar="[NAME]", required=False, default=None)
@click.option(
    "--name",
    "name_opt",
    default=None,
    help="The artifact's NAME, as an alternative to passing it positionally.",
)
@click.option(
    "--node",
    "node",
    default=None,
    help="The producing node's name, to read that one node's series of a NAME several nodes emit "
    "(a bare read returns the newest). Neither "
    "a graph declaration nor a system artifact has a producing node, so this narrows to node "
    "scope on its own — pairing it with `--scope graph`/`--scope system` is a contradiction "
    "and is refused.",
)
@click.option(
    "--scope",
    "scope",
    type=_SCOPE_CHOICE,
    default=None,
    help="Resolve NAME from one scope only — `node`, `graph`, or `system`. Omitted searches "
    "all three, and a NAME present in more than one scope is ambiguous — unless `--node` narrows to node scope.",
)
@click.option(
    "--content",
    "content",
    is_flag=True,
    default=False,
    help="Print the raw asset text to stdout instead of JSON (errors on a git-commit artifact).",
)
def artifact_get(
    name_arg: str | None, name_opt: str | None, node: str | None, scope: str | None, content: bool
) -> None:
    """Worker: read one published artifact by NAME, passed literally, slashes included — a ``produces:`` name
    (node scope), a baked-in graph declaration (graph scope), or one of blizzard's own published documents
    (system scope). Refused when NAME is unknown, when NAME resolves in more than one scope (naming them), or when
    this node-step has only staged it (pointing at ``artifact staged``). ``--content`` prints raw asset text,
    refused for a git-commit artifact, which carries none."""
    name = name_opt if name_opt is not None else name_arg
    if name_arg is not None and name_opt is not None and name_arg != name_opt:
        raise click.ClickException("artifact get: NAME given both positionally and via --name — pick one")
    if not name:
        raise click.ClickException("artifact get: NAME is required, positionally or via --name")
    worker = WorkerCall.of("artifact get")
    params: dict[str, str] = {}
    if node:
        params["node"] = node
    if scope:
        params["scope"] = scope
    try:
        resp = worker.get(
            worker.leased(f"artifacts/{quote(name, safe='/')}"),
            failure=f"could not read {name!r}",
            params=params or None,
        )
    except click.ClickException as exc:
        staged_scope = scope in (None, ArtifactScope.NODE.value)
        if _is_not_found(exc) and staged_scope and name in (_staged_names(worker) or set()):
            raise click.ClickException(
                f"artifact get: {name!r} is staged but not yet published for this node-step — "
                "read it with `artifact staged` (it publishes into `artifact get` only once "
                "this node-step completes)"
            ) from exc
        raise
    _, artifact = decode_answer(resp, TypeAdapter(WorkerArtifact), verb="artifact get", where="artifacts")
    if not content:
        click.echo(resp.text)
        return
    kind = artifact.kind
    if isinstance(kind, Unrecognized) and artifact.content is None:
        raise click.ClickException(
            f"artifact get: {name!r} is a {kind.raw!r} artifact, a kind this CLI does not know, "
            "and carries no content (drop --content to read it whole)"
        )
    if isinstance(kind, ArtifactKind) and not carries_content(kind):
        raise click.ClickException(
            f"artifact get: {name!r} is a git-commit artifact — it has no content (drop --content to read its ref)"
        )
    # Raw, un-decorated: the asset text as stored, no added trailing newline.
    click.echo(artifact.content or "", nl=False)


@artifact_group.command("create")
@click.option("--name", required=True, help="The `produces:` name this content is submitted for.")
@click.option(
    "--scope",
    "scope",
    type=_SCOPE_CHOICE,
    default=None,
    help="Always `node` — `graph` and `system` are read-only and refused.",
)
def artifact_create(name: str, scope: str | None) -> None:
    """Worker: durably submit an asset artifact for a ``produces:`` NAME (content on stdin), node
    scope only. A submission *stages* the content, published into the envelope only on completion
    — read it back with ``artifact staged``. Empty stdin is refused locally. Content the hub's
    check refuses — it fails the format the chunk's graph's delivery applies to NAME — is refused
    with the delivery-time message and nothing stored. When the hub gives no usable verdict the
    content is stored unchecked. A recorded submission is a durable local save, never acceptance:
    delivery still judges it. Any refusal exits non-zero rather than silently losing the
    submission."""
    _refuse_read_only_scope("create", scope)
    worker = WorkerCall.of("artifact create")
    content = click.get_text_stream("stdin").read()
    if not content:
        raise click.ClickException(
            "artifact create: empty stdin — refusing to submit an empty artifact "
            "(any previously staged submission for this name is untouched)"
        )
    resp = worker.post(
        worker.leased("attachments"),
        failure=f"could not record {name!r}",
        rejected=f"could not record {name!r}, nothing was stored",
        json_body={"name": name, "content": content},
    )
    _, body = decode_answer(resp, TypeAdapter(AttachmentResponse), verb="artifact create", where="attachments")
    click.echo(f"recorded {body.name!r} ({body.bytes} bytes)")


@artifact_group.command("staged")
@click.option(
    "--content",
    "content",
    is_flag=True,
    default=False,
    help="Include each staged submission's full content instead of just its byte length.",
)
@click.option(
    "--scope",
    "scope",
    type=_SCOPE_CHOICE,
    default=None,
    help="Always `node` — `graph` and `system` hold no staged submission and are refused.",
)
def artifact_staged(content: bool, scope: str | None) -> None:
    """Worker: list this node-step's own staged (not-yet-published) submissions, node scope only.
    A fresh ``artifact create`` shows up here immediately; ``--content`` gives the full text."""
    _refuse_read_only_scope("staged", scope)
    worker = WorkerCall.of("artifact staged")
    resp = worker.get(worker.leased("attachments"), failure="could not read the staged artifacts")
    _, staged = decode_answer(resp, TypeAdapter(list[StagedAttachment]), verb="artifact staged", where="attachments")
    if content:
        click.echo(resp.text)
        return
    click.echo(json.dumps([{"name": a.name, "bytes": len(a.content.encode("utf-8"))} for a in staged]))


@artifact_group.command("commit")
@click.option(
    "--env",
    "environment_id",
    default=None,
    help="The leased environment the repo worktree lives in. Inferred while a chunk "
    "holds exactly one environment; required once it holds several.",
)
@click.option(
    "--repo",
    required=True,
    help="The repo's name in the environment's repo manifest (not an `owner/name` slug or "
    "URL). A name the manifest does not list is refused, naming the repos that are.",
)
@click.option("--branch", required=True, help="The branch the commit was pushed to.")
@click.option(
    "--commit",
    "commit_sha",
    required=True,
    help="The FULL commit sha (`git rev-parse HEAD`); an abbreviated sha fails verification against the pushed commit.",
)
@click.option(
    "--scope",
    "scope",
    type=_SCOPE_CHOICE,
    default=None,
    help="Always `node` — `graph` and `system` are read-only and refused.",
)
def artifact_commit(environment_id: str | None, repo: str, branch: str, commit_sha: str, scope: str | None) -> None:
    """Worker: durably declare a git-commit artifact for REPO. Carries the ``git_commit``
    kind only — an asset is declared through ``artifact create``. Node scope only. Echoes a
    confirmation naming REPO, BRANCH, and the sha on success."""
    _refuse_read_only_scope("commit", scope)
    worker = WorkerCall.of("artifact commit")
    body: dict[str, str] = {"repo": repo, "branch": branch, "commit": commit_sha}
    if environment_id is not None:
        body["environment_id"] = environment_id
    resp = worker.post(
        worker.leased("git-commits"),
        failure=f"could not record {repo!r}",
        rejected=f"{repo!r} rejected",
        json_body=body,
    )
    _, answer = decode_answer(
        resp, TypeAdapter(GitCommitDeclarationResponse), verb="artifact commit", where="git-commits"
    )
    click.echo(f"recorded {repo!r} at {branch!r} ({commit_sha})")
    note = answer.note
    if note:
        click.echo(note)
