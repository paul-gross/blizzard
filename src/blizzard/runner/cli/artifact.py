from __future__ import annotations

import json
from dataclasses import dataclass
from urllib.parse import quote

import click

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
    """``create``/``commit``/``staged`` are node-scope only: a graph's declarations are baked at
    mint and a system artifact is blizzard's own published document — both read-only. Refusing
    here states that domain fact to a worker parsing stderr mid-turn (which scopes each verb
    serves: ``blizzard-context:/standards/worker-nodes/declarations.md``)."""
    reason = _READ_ONLY_SCOPE_REASON.get(scope or "")
    if reason is not None:
        raise click.ClickException(f"artifact {verb}: {scope} scope is read-only — {reason}")


_SCOPE_CHOICE = click.Choice([s.value for s in ArtifactScope])


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
    every upstream asset's full text has overflowed tool output; ``--content`` restores it."""
    worker = WorkerCall.of("artifact list")
    resp = worker.get(
        worker.leased("artifacts"),
        failure="could not read the artifacts",
        params={"scope": scope} if scope else None,
    )
    if content:
        click.echo(resp.text)
        return
    click.echo(json.dumps([ArtifactEntry(a).summary for a in resp.json()]))


@artifact_group.command("get")
@click.argument("name")
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
def artifact_get(name: str, node: str | None, scope: str | None, content: bool) -> None:
    """Worker: read one artifact by NAME — a ``produces:`` name (node scope), a baked-in graph
    declaration (graph scope), or one of blizzard's own published documents (system scope);
    unknown is a ``404``, more than one candidate a ``409`` naming them. ``--content`` prints
    raw asset text, and errors on the ``git_commit`` kind, which carries none. NAME is
    percent-encoded (issue #233)."""
    worker = WorkerCall.of("artifact get")
    params: dict[str, str] = {}
    if node:
        params["node"] = node
    if scope:
        params["scope"] = scope
    resp = worker.get(
        worker.leased(f"artifacts/{quote(name, safe='/')}"),
        failure=f"could not read {name!r}",
        params=params or None,
    )
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
    scope only — ``--scope graph``/``--scope system`` are refused, both being read-only.
    A submission *stages* the content, published into the envelope only on completion (issue #169)
    — read it back with ``artifact staged``. Empty stdin and any rejection exit non-zero rather
    than silently losing the submission."""
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
    """Worker: list this node-step's own staged (not-yet-published) submissions, node scope only
    — ``--scope graph``/``--scope system`` are refused, neither ever being staged. Read straight
    off the runner's own ``attachments`` record rather than the hub envelope (issue #169), so a
    fresh ``artifact create`` shows up here immediately; ``--content`` gives the full text."""
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
    kind only — an asset is declared through ``artifact create``. Node scope only —
    ``--scope graph``/``--scope system`` are refused. Deliberately no ``--forge``: the origin
    comes from the environment's repo manifest (pinned by
    tests/test_runner_artifact_commit_cli.py::test_commit_verb_has_no_forge_flag)."""
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
