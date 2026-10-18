"""``blizzard-compat``: hold HEAD's committed surface documents to the supported releases.

    blizzard-compat releases [--json] [--line release/X.Y]   the supported releases themselves
    blizzard-compat [--commits BASE..HEAD] [--line release/X.Y]   HEAD against them

HEAD's documents and window are read from the working tree; a release's documents, and the window at
the range's base, only through ``git show``. Nothing at a tag is imported or run. This module is the
composition root: it alone reads the packaged window type and maps it to compat's own ``Window``."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from blizzard.foundation.roles import domain_model
from blizzard.tools.compat import gate
from blizzard.tools.compat.git import GitError, Repository
from blizzard.tools.compat.releases import (
    Release,
    Window,
    line_of,
    newest_stable,
    on_line,
    releases_document,
    supported,
)
from blizzard.tools.compat.surfaces.api import classify_api
from blizzard.tools.compat.surfaces.config import classify_config
from blizzard.tools.compat.surfaces.egress import classify_egress
from blizzard.tools.compat.surfaces.store import classify_store
from blizzard.tools.compat.surfaces.traces import classify_traces
from blizzard.tools.compat.surfaces.wire import classify_spec_diff
from blizzard.tools.compat.violation import Violation
from blizzard.wire.version_window import VersionWindow, VersionWindowError

_WINDOW_PATH = "src/blizzard/wire/window.toml"

type Classifier = Callable[[dict[str, Any], dict[str, Any]], list[Violation]]


def _classify_hub_api(base: dict[str, Any], head: dict[str, Any]) -> list[Violation]:
    wire = classify_spec_diff(base, head)
    return [*wire, *classify_api(base, head, wire=wire)]


#: Each committed document, the surface(s) it carries, and the classifier that diffs it.
_DOCUMENTS: tuple[tuple[str, str, Classifier], ...] = (
    ("openapi/hub.openapi.json", "wire, http-api", _classify_hub_api),
    ("openapi/runner.openapi.json", "http-api", classify_api),
    ("contracts/config/hub.json", "config", classify_config),
    ("contracts/config/host.json", "config", classify_config),
    ("contracts/traces/dictionary.json", "trace", classify_traces),
    ("contracts/egress/dictionary.json", "egress", classify_egress),
    ("contracts/store/hub.json", "store", classify_store),
    ("contracts/store/runner.json", "store", classify_store),
)


@domain_model
@dataclass(frozen=True)
class Outcome:
    """One document against one release: its findings, or ``None`` where the release has no baseline."""

    document: str
    surface: str
    findings: tuple[Violation, ...] | None


def _window(version_window: VersionWindow) -> Window:
    return Window(floor=version_window.floor, opening=version_window.opening)


def _head_documents(root: Path) -> dict[str, dict[str, Any]]:
    documents = {}
    for path, _surface, _classify in _DOCUMENTS:
        try:
            documents[path] = json.loads((root / path).read_text())
        except OSError as error:
            raise click.ClickException(f"HEAD has no {path}: {error}") from error
    return documents


def _check_release(repo: Repository, rev: str, head: dict[str, dict[str, Any]]) -> list[Outcome]:
    outcomes = []
    for path, surface, classify in _DOCUMENTS:
        text = repo.show(rev, path)
        findings = None if text is None else tuple(classify(json.loads(text), head[path]))
        outcomes.append(Outcome(path, surface, findings))
    return outcomes


def _findings(outcomes: Sequence[Outcome]) -> list[Violation]:
    return [v for outcome in outcomes for v in outcome.findings or ()]


def _print_release(release: str, outcomes: Sequence[Outcome], verdicts: Sequence[gate.Verdict]) -> None:
    click.echo(f"{release}:")
    allowed = {(v.release, v.violation): v.passes for v in verdicts}
    for outcome in outcomes:
        if outcome.findings is None:
            click.echo(f"  {outcome.document} ({outcome.surface}): no baseline — {release} has no {outcome.document}")
        elif not outcome.findings:
            click.echo(f"  {outcome.document} ({outcome.surface}): additive")
        else:
            click.echo(f"  {outcome.document} ({outcome.surface}):")
            for finding in outcome.findings:
                state = "allowed" if allowed[(release, finding)] else "BREAK"
                mark = ", deprecated in baseline" if finding.deprecated else ""
                click.echo(f"    - [{state}] {finding.surface} {finding}{mark}")


def _read_head_window(root: Path) -> Window:
    try:
        return _window(VersionWindow.load(root / _WINDOW_PATH))
    except VersionWindowError as error:
        raise click.ClickException(str(error)) from error


def _read_base_window(repo: Repository, rev: str) -> Window | None:
    text = repo.show(rev, _WINDOW_PATH)
    if text is None:
        return None
    try:
        return _window(VersionWindow.parse(text, f"{rev}:{_WINDOW_PATH}"))
    except VersionWindowError as error:
        raise click.ClickException(str(error)) from error


def _selected(tags: list[str], window: Window, line: str | None) -> list[Release]:
    return on_line(tags, line_of(line)) if line else supported(tags, window)


def _resolve(line: str | None, tags: list[str], window: Window) -> list[Release]:
    try:
        return _selected(tags, window, line)
    except ValueError as error:
        raise click.UsageError(str(error)) from error


def _split_range(commits: str) -> str:
    base, separator, _head = commits.partition("..")
    if not separator or not base or commits.count("..") != 1 or "..." in commits:
        raise click.UsageError(f"--commits takes BASE..HEAD, got {commits!r}")
    return base


@click.group(invoke_without_command=True)
@click.option("--commits", default=None, help="BASE..HEAD: the range whose commit subjects may carry a `!`.")
@click.option("--line", default=None, help="release/X.Y: hold HEAD to every release cut on that line instead.")
@click.pass_context
def main(context: click.Context, commits: str | None, line: str | None) -> None:
    """Hold HEAD's surface documents to the supported releases."""
    if context.invoked_subcommand is not None:
        return
    try:
        _run(commits, line)
    except GitError as error:
        raise click.ClickException(str(error)) from error


def _run(commits: str | None, line: str | None) -> None:
    repo = Repository(Path.cwd())
    root = repo.toplevel()
    repo = Repository(root)
    tags = repo.tags()
    window = _read_head_window(root)
    releases = _resolve(line, tags, window)
    click.echo("supported releases: " + (", ".join(r.tag for r in releases) or "none"))
    acknowledged = gate.is_acknowledged(repo.subjects(commits)) if commits else False
    if commits and not line:
        click.echo(f"change acknowledges a break with `!`: {'yes' if acknowledged else 'no'}")

    head_documents = _head_documents(root)
    outcomes = {r.tag: _check_release(repo, r.tag, head_documents) for r in releases}
    verdicts = gate.judge(
        {tag: _findings(o) for tag, o in outcomes.items()}, acknowledged=acknowledged, line=bool(line)
    )
    if releases:
        for release in releases:
            _print_release(release.tag, outcomes[release.tag], verdicts)
    else:
        click.echo("no release is supported yet; nothing has shipped that this change could break.")

    window_findings: list[Violation] = []
    if commits and not line:
        base = _read_base_window(repo, _split_range(commits))
        base_verdicts: list[gate.Verdict] = []
        if base is not None and base.floor_line < window.floor_line:
            base_outcomes = {r.tag: _check_release(repo, r.tag, head_documents) for r in supported(tags, base)}
            base_verdicts = gate.judge(
                {tag: _findings(o) for tag, o in base_outcomes.items()}, acknowledged=acknowledged, line=False
            )
        stable = newest_stable(tags)
        window_findings = gate.judge_window(
            window,
            base,
            base_verdicts=base_verdicts,
            head_verdicts=verdicts,
            newest_stable_major=stable.major if stable else None,
        )
    for finding in window_findings:
        click.echo(f"window: [BREAK] {finding.where}: {finding.reason}")

    raise SystemExit(1 if window_findings or any(not v.passes for v in verdicts) else 0)


@main.command()
@click.option("--json", "as_json", is_flag=True, help="The releases as the stable JSON document other checks read.")
@click.option("--line", default=None, help="release/X.Y: list every release cut on that line instead.")
def releases(as_json: bool, line: str | None) -> None:
    """The supported releases of HEAD's tree, oldest first."""
    repo = Repository(Path.cwd())
    root = repo.toplevel()
    window = _read_head_window(root)
    listed = _resolve(line, Repository(root).tags(), window)
    if as_json:
        click.echo(json.dumps(releases_document(window, listed), indent=2))
    elif listed:
        for release in listed:
            click.echo(release.tag)
    else:
        click.echo("no release is supported yet")


if __name__ == "__main__":
    main()
