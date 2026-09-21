from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import quote

import click
import httpx

from blizzard.foundation.artifacts import ArtifactKind, ArtifactScope
from blizzard.runner.cli.worker_call import WorkerCall


@click.group("artifact")
def artifact_group() -> None:
    """Worker: read node-step, graph, and system artifacts; write this node-step's own (issue
    #127). The lease binding is ambient: every verb acts on the worker's own lease, resolved
    from the spawn environment — none takes a flag naming another chunk. ``--scope`` picks node
    scope, the graph mint's baked-in declarations, or blizzard's published system-artifact set.
    ``create`` *stages* a submission, published on completion (#169)."""


@dataclass(frozen=True)
class ArtifactEntry:
    """One ``list``-view entry (issue #169) — every field but ``content``, which collapses to
    its ``bytes`` length (``None`` when the artifact carries none, i.e. ``git_commit``).
    Carries ``scope`` (node/graph/system) like every other field."""

    artifact: dict

    @property
    def summary(self) -> dict:
        content = self.artifact.get("content")
        summary = {k: v for k, v in self.artifact.items() if k != "content"}
        summary["bytes"] = len(content.encode("utf-8")) if content is not None else None
        return summary


#: Why each read-only scope refuses a write — named so the refusal states the domain fact,
#: not just the word "read-only".
_READ_ONLY_SCOPE_REASON = {
    ArtifactScope.GRAPH.value: "a graph's declarations are baked at mint",
    ArtifactScope.SYSTEM.value: "a system artifact is published by blizzard itself",
}


def _refuse_read_only_scope(verb: str, scope: str | None) -> None:
    """``create``/``commit``/``staged`` are node-scope only; refuses using
    ``_READ_ONLY_SCOPE_REASON``'s table (``blizzard-context:/standards/worker-nodes/declarations.md``)."""
    reason = _READ_ONLY_SCOPE_REASON.get(scope or "")
    if reason is not None:
        raise click.ClickException(f"artifact {verb}: {scope} scope is read-only — {reason}")


_SCOPE_CHOICE = click.Choice([s.value for s in ArtifactScope])


def _staged_for_scope(worker: WorkerCall, scope: str | None) -> list[dict]:
    """This node-step's own staged (not-yet-published) submissions, or ``[]`` for a SCOPE that
    excludes node — ``graph`` and ``system`` never have one, so no read is worth making. Also
    ``[]`` on a failed read: by the time this runs, ``list``'s primary ``artifacts`` call has
    already succeeded, and a value-add feature (staged visibility) must never take that
    already-successful read down with it."""
    if scope not in (None, ArtifactScope.NODE.value):
        return []
    try:
        resp = worker.get(worker.leased("attachments"), failure="could not read the staged artifacts")
    except click.ClickException as exc:
        click.echo(f"artifact list: {exc.message}; omitting staged entries", err=True)
        return []
    return resp.json()


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
    set — ``--scope`` narrows to one. Content is elided by default (issue #169), since inlining
    every upstream asset's full text has overflowed tool output; ``--content`` restores it.
    Also includes this node-step's own staged, not-yet-published submissions (issue #584),
    each carrying ``"staged": true`` — everything published carries ``"staged": false``."""
    worker = WorkerCall.of("artifact list")
    resp = worker.get(
        worker.leased("artifacts"),
        failure="could not read the artifacts",
        params={"scope": scope} if scope else None,
    )
    staged = _staged_for_scope(worker, scope)
    entries = [{**a, "staged": False} for a in resp.json()] + [{**a, "staged": True} for a in staged]
    if content:
        click.echo(json.dumps(entries))
        return
    click.echo(json.dumps([ArtifactEntry(e).summary for e in entries]))


def _staged_names(worker: WorkerCall) -> set[str] | None:
    """The names of this node-step's own staged submissions, or ``None`` on a failed read — a
    failed staged-check must never mask the original not-found error."""
    try:
        resp = worker.get(worker.leased("attachments"), failure="could not read the staged artifacts")
    except click.ClickException:
        return None
    return {a["name"] for a in resp.json()}


def _is_not_found(exc: click.ClickException) -> bool:
    """Only a genuine ``404`` is worth a second, staged-set lookup — any other rejection (a
    ``409`` ambiguity, a ``403``) already names its own cause and gets no staged detour."""
    cause = exc.__cause__
    if not isinstance(cause, httpx.HTTPStatusError):
        return False
    return getattr(cause.response, "status_code", None) == httpx.codes.NOT_FOUND


@artifact_group.command("get")
@click.argument("name_arg", metavar="[NAME]", required=False, default=None)
@click.option(
    "--name",
    "name_opt",
    default=None,
    help="Alias for the positional NAME, accepted since node prompts spell required artifacts "
    "as `--name` (issue #584).",
)
@click.option(
    "--node",
    "node",
    default=None,
    help="The producing node's name, to disambiguate a NAME more than one node emits. Neither "
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
    "all three, and a NAME present in more than one is ambiguous the same as several "
    "producing nodes — unless `--node` settles it.",
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
    """Worker: read one artifact by NAME — a ``produces:`` name (node scope), a baked-in graph
    declaration (graph scope), or one of blizzard's own published documents (system scope);
    unknown is a ``404``, more than one candidate a ``409`` naming them. ``--content`` prints
    raw asset text, and errors on the ``git_commit`` kind, which carries none.

    Serves the last *published* epoch only — a node-step's own just-submitted content is not
    published until the node-step completes; a not-yet-published NAME 404s naming
    ``artifact staged`` instead. Read it back before completion with that verb.

    NAME is passed literally: the CLI percent-encodes it itself (issue #233), slashes
    included, so a slashed name (e.g. a ``merged/<owner>/<repo>`` delivery marker) is passed
    as-is, not pre-encoded."""
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
    if not content:
        click.echo(resp.text)
        return
    artifact = resp.json()
    if artifact.get("kind") == ArtifactKind.GIT_COMMIT:
        raise click.ClickException(
            f"artifact get: {name!r} is a git-commit artifact — it has no content (drop --content to read its ref)"
        )
    # Raw, un-decorated: the asset text as stored, no added trailing newline.
    click.echo(artifact.get("content") or "", nl=False)


@artifact_group.command("create")
@click.option("--name", required=True, help="The `produces:` name this content is submitted for.")
@click.option(
    "--scope",
    "scope",
    type=_SCOPE_CHOICE,
    default=None,
    help="Always `node` — `graph` and `system` are refused, since a graph-mint declaration and "
    "a system artifact are both read-only.",
)
def artifact_create(name: str, scope: str | None) -> None:
    """Worker: durably submit an asset artifact for a ``produces:`` NAME (content on stdin), node
    scope only. A submission *stages* the content, published into the envelope only on completion
    (issue #169) — read it back with ``artifact staged``. Empty stdin and any rejection exit
    non-zero rather than silently losing the submission."""
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
        json_body={"name": name, "content": content},
    )
    body = resp.json()
    click.echo(f"recorded {body.get('name', name)!r} ({body.get('bytes', len(content.encode('utf-8')))} bytes)")


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
    help="Always `node` — `graph` and `system` are refused, neither ever having a staged submission.",
)
def artifact_staged(content: bool, scope: str | None) -> None:
    """Worker: list this node-step's own staged (not-yet-published) submissions, node scope only.
    Read straight off the runner's own ``attachments`` record rather than the hub envelope (issue
    #169), so a fresh ``artifact create`` shows up here immediately; ``--content`` gives the full
    text."""
    _refuse_read_only_scope("staged", scope)
    worker = WorkerCall.of("artifact staged")
    resp = worker.get(worker.leased("attachments"), failure="could not read the staged artifacts")
    if content:
        click.echo(resp.text)
        return
    staged = resp.json()
    click.echo(json.dumps([{"name": a["name"], "bytes": len(a["content"].encode("utf-8"))} for a in staged]))


@artifact_group.command("commit")
@click.option(
    "--env",
    "environment_id",
    default=None,
    help="The leased environment the repo worktree lives in. Optional while a chunk "
    "holds exactly one environment (it is inferred); required once it holds several, "
    "since the same repo has a worktree in each.",
)
@click.option(
    "--repo",
    required=True,
    help="The repo's name in the leased env's manifest (not an `owner/name` slug or "
    "URL) — the runner looks this up in the environment's repo manifest to find both "
    "the worktree and the origin to verify against. A name the manifest does not list "
    "is rejected outright, naming the repos that are.",
)
@click.option("--branch", required=True, help="The branch the commit was pushed to.")
@click.option(
    "--commit",
    "commit_sha",
    required=True,
    help="The FULL commit sha (`git rev-parse HEAD`), not an abbreviated form — verify "
    "compares it byte-exact against the forge's full sha.",
)
@click.option(
    "--scope",
    "scope",
    type=_SCOPE_CHOICE,
    default=None,
    help="Always `node` — `graph` and `system` are refused, since a graph-mint declaration and "
    "a system artifact are both read-only.",
)
def artifact_commit(environment_id: str | None, repo: str, branch: str, commit_sha: str, scope: str | None) -> None:
    """Worker: durably declare a git-commit artifact for REPO (issue #143). Carries the ``git_commit``
    kind only — an asset is declared through ``artifact create``. Node scope only. Deliberately no
    ``--forge``: the origin comes from the environment's repo manifest (pinned by
    tests/test_runner_artifact_commit_cli.py::test_commit_verb_has_no_forge_flag). Echoes a
    confirmation naming REPO, BRANCH, and the sha on success (issue #584) — a silent exit 0 was
    indistinguishable from a no-op."""
    _refuse_read_only_scope("commit", scope)
    worker = WorkerCall.of("artifact commit")
    body: dict[str, str] = {"repo": repo, "branch": branch, "commit": commit_sha}
    if environment_id is not None:
        body["environment_id"] = environment_id
    worker.post(
        worker.leased("git-commits"),
        failure=f"could not record {repo!r}",
        rejected=f"{repo!r} rejected",
        json_body=body,
    )
    click.echo(f"recorded {repo!r} at {branch!r} ({commit_sha})")
