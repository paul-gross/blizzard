"""``blizzard-compat``: hold HEAD's committed surface documents to the supported releases.

    blizzard-compat releases [--json] [--line release/X.Y]   the supported releases themselves
    blizzard-compat [--commits BASE..HEAD] [--line release/X.Y] [--json]   HEAD against them
    blizzard-compat --tag VERSION [--json]   HEAD judged as the tag v<VERSION>; writes compat-breaks.json

HEAD's documents and window are read from the working tree; a release's documents, and the window at
the range's base, only through ``git show``. Nothing at a tag is imported or run. This module is the
composition root: it alone reads the packaged window type and maps it to compat's own ``Window``."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from blizzard.foundation.roles import domain_model
from blizzard.tools.change_notes.contract import Note
from blizzard.tools.change_notes.tree import CHANGES_DIR, README, read_tree
from blizzard.tools.compat import gate
from blizzard.tools.compat.git import GitError, Repository
from blizzard.tools.compat.releases import (
    Release,
    Window,
    line_of,
    newest_stable,
    on_line,
    parse_release,
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
BREAKS_FILE = "compat-breaks.json"

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
        findings = None if text is None or path not in head else tuple(classify(json.loads(text), head[path]))
        outcomes.append(Outcome(path, surface, findings))
    return outcomes


def _findings(outcomes: Sequence[Outcome]) -> list[Violation]:
    return [v for outcome in outcomes for v in outcome.findings or ()]


def _state(verdict: gate.Verdict) -> str:
    if verdict.inherited:
        return "inherited"
    return "allowed" if verdict.passes else "BREAK"


def _print_release(release: str, outcomes: Sequence[Outcome], verdicts: Sequence[gate.Verdict]) -> None:
    click.echo(f"{release}:")
    states = {(v.release, v.violation): _state(v) for v in verdicts}
    for outcome in outcomes:
        if outcome.findings is None:
            click.echo(f"  {outcome.document} ({outcome.surface}): no baseline — {release} has no {outcome.document}")
        elif not outcome.findings:
            click.echo(f"  {outcome.document} ({outcome.surface}): additive")
        else:
            click.echo(f"  {outcome.document} ({outcome.surface}):")
            for finding in outcome.findings:
                mark = ", deprecated in baseline" if finding.deprecated else ""
                click.echo(f"    - [{states[(release, finding)]}] {finding.surface} {finding}{mark}")


def _entry(verdict: gate.Verdict) -> dict[str, Any]:
    """One finding as the ``--json`` document and ``compat-breaks.json`` carry it; a field is only ever added."""
    violation = verdict.violation
    return {
        "release": verdict.release,
        "surface": violation.surface,
        "where": violation.where,
        "reason": violation.reason,
        "deprecated": violation.deprecated,
        "inherited": verdict.inherited,
        "passes": verdict.passes,
    }


def _sorted_entries(verdicts: Sequence[gate.Verdict]) -> list[dict[str, Any]]:
    return sorted((_entry(v) for v in verdicts), key=lambda e: (e["release"], e["surface"], e["where"], e["reason"]))


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


def _note_ids(paths: Sequence[str]) -> set[str]:
    return {Path(p).stem for p in paths if p.endswith(".md") and Path(p).name != README}


def _declared_pairs(note: Note) -> set[tuple[str, str]]:
    """The ``(surface, where)`` a breaking note declares: each of its surfaces against each name in ``breaks``."""
    if note.fields.get("breaking") is not True:
        return set()
    surfaces, names = note.fields.get("surfaces") or [], note.fields.get("breaks") or []
    return {(str(surface), str(name).strip()) for surface in surfaces for name in names}


def _declared(repo: Repository, root: Path, base: str) -> frozenset[tuple[str, str]]:
    """The breaks declared by the notes HEAD adds over ``base``, by note id (a note the cut moved still counts)."""
    added = _note_ids(repo.paths("HEAD", CHANGES_DIR)) - _note_ids(repo.paths(base, CHANGES_DIR))
    if not added or not (root / CHANGES_DIR).is_dir():
        return frozenset()
    notes, _failures = read_tree(root)
    return frozenset(pair for note in notes if note.id in added for pair in _declared_pairs(note))


def _inherited(
    repo: Repository, base: str, releases: Sequence[Release], base_documents: dict[str, dict[str, Any]]
) -> set[tuple[str, str, str, str]]:
    """The ``(release, surface, where, reason)`` of every finding the base tree already carries against ``releases``."""
    return {
        (release.tag, finding.surface, finding.where, finding.reason)
        for release in releases
        for finding in _findings(_check_release(repo, release.tag, base_documents))
    }


def _base_documents(repo: Repository, rev: str) -> dict[str, dict[str, Any]]:
    documents = {}
    for path, _surface, _classify in _DOCUMENTS:
        text = repo.show(rev, path)
        if text is not None:
            documents[path] = json.loads(text)
    return documents


@contextmanager
def _git_errors() -> Iterator[None]:
    """A failed git call ends the command with its one-line message, not a traceback."""
    try:
        yield
    except GitError as error:
        raise click.ClickException(str(error)) from error


@click.group(invoke_without_command=True)
@click.option("--commits", default=None, help="BASE..HEAD: the range whose commit subjects may carry a `!`.")
@click.option("--line", default=None, help="release/X.Y: hold HEAD to every release cut on that line instead.")
@click.option("--tag", default=None, help="VERSION: judge HEAD as the tag v<VERSION> and write compat-breaks.json.")
@click.option("--json", "as_json", is_flag=True, help="Print the findings as a JSON document instead of text.")
@click.pass_context
def main(context: click.Context, commits: str | None, line: str | None, tag: str | None, as_json: bool) -> None:
    """Hold HEAD's surface documents to the supported releases."""
    if context.invoked_subcommand is not None:
        return
    if tag is not None and (commits or line):
        raise click.UsageError("--tag cannot be combined with --commits or --line")
    with _git_errors():
        if tag is not None:
            _run_tag(tag, as_json)
        else:
            _run(commits, line, as_json)


def _emit(
    as_json: bool,
    releases: Sequence[Release],
    outcomes: dict[str, list[Outcome]],
    verdicts: Sequence[gate.Verdict],
    window_findings: Sequence[Violation],
) -> None:
    if as_json:
        click.echo(
            json.dumps(
                {
                    "releases": [r.tag for r in releases],
                    "findings": _sorted_entries(verdicts),
                    "window": [{"where": f.where, "reason": f.reason} for f in window_findings],
                },
                indent=2,
            )
        )
        return
    if releases:
        for release in releases:
            _print_release(release.tag, outcomes[release.tag], verdicts)
    else:
        click.echo("no release is supported yet; nothing has shipped that this change could break.")
    for finding in window_findings:
        click.echo(f"window: [BREAK] {finding.where}: {finding.reason}")


def _run(commits: str | None, line: str | None, as_json: bool) -> None:
    repo = Repository(Path.cwd())
    root = repo.toplevel()
    repo = Repository(root)
    tags = repo.tags()
    window = _read_head_window(root)
    releases = _resolve(line, tags, window)
    say = (lambda _text: None) if as_json else click.echo
    say("supported releases: " + (", ".join(r.tag for r in releases) or "none"))
    base_rev = _split_range(commits) if commits else None
    acknowledged = gate.is_acknowledged(repo.subjects(commits)) if commits else False
    if commits and not line:
        say(f"change acknowledges a break with `!`: {'yes' if acknowledged else 'no'}")
    declared = _declared(repo, root, base_rev) if base_rev else frozenset()
    base_documents = _base_documents(repo, base_rev) if base_rev else {}

    def judged(selected: Sequence[Release], documents: dict[str, dict[str, Any]]) -> dict[str, list[Outcome]]:
        return {r.tag: _check_release(repo, r.tag, documents) for r in selected}

    def judge(outcomes: dict[str, list[Outcome]], selected: Sequence[Release]) -> list[gate.Verdict]:
        return gate.judge(
            {tag: _findings(o) for tag, o in outcomes.items()},
            acknowledged=acknowledged,
            line=bool(line),
            declarable=window.opening is not None,
            declared=declared,
            inherited=_inherited(repo, base_rev, selected, base_documents) if base_rev else (),
        )

    head_documents = _head_documents(root)
    outcomes = judged(releases, head_documents)
    verdicts = judge(outcomes, releases)

    window_findings: list[Violation] = []
    if base_rev and not line:
        assert commits
        base = _read_base_window(repo, base_rev)
        base_verdicts: list[gate.Verdict] = []
        if base is not None and base.floor_line < window.floor_line:
            base_releases = supported(tags, base)
            base_verdicts = judge(judged(base_releases, head_documents), base_releases)
        stable = newest_stable(tags)
        window_findings = gate.judge_window(
            window,
            base,
            base_verdicts=base_verdicts,
            head_verdicts=verdicts,
            newest_stable_major=stable.major if stable else None,
        )
    _emit(as_json, releases, outcomes, verdicts, window_findings)
    raise SystemExit(1 if window_findings or any(not v.passes for v in verdicts) else 0)


def _run_tag(version: str, as_json: bool) -> None:
    """Judge HEAD as the tag ``v<version>`` against each supported release, each read over its own ``R..HEAD``.

    A candidate or release above every stable major admits what an open ``master`` admits; any other minor or
    major admits deprecated breaks only; a patch admits nothing. Always writes ``compat-breaks.json``."""
    candidate = parse_release(f"v{version}")
    if candidate is None:
        raise click.UsageError(f"--tag takes MAJOR.MINOR.PATCH or MAJOR.MINOR.PATCH-rc.N, got {version!r}")
    repo = Repository(Path.cwd())
    root = repo.toplevel()
    repo = Repository(root)
    tags = [t for t in repo.tags() if t != candidate.tag]
    window = _read_head_window(root)
    releases = _resolve(None, tags, window)
    stable = newest_stable(tags)
    patch = candidate.patch >= 1
    declarable = not patch and (stable is None or candidate.major > stable.major)
    head_documents = _head_documents(root)
    outcomes = {r.tag: _check_release(repo, r.tag, head_documents) for r in releases}
    verdicts: list[gate.Verdict] = []
    for release in releases:
        verdicts += gate.judge(
            {release.tag: _findings(outcomes[release.tag])},
            acknowledged=gate.is_acknowledged(repo.subjects(f"{release.tag}..HEAD")),
            line=patch,
            declarable=declarable,
            declared=_declared(repo, root, release.tag),
        )
    breaks = [
        {key: entry[key] for key in ("release", "surface", "where", "reason")}
        for entry in _sorted_entries([v for v in verdicts if v.passes])
    ]
    (root / BREAKS_FILE).write_text(json.dumps({"tag": candidate.tag, "breaks": breaks}, indent=2) + "\n")
    if not as_json:
        click.echo(f"judging {candidate.tag}; supported releases: " + (", ".join(r.tag for r in releases) or "none"))
    _emit(as_json, releases, outcomes, verdicts, [])
    raise SystemExit(1 if any(not v.passes for v in verdicts) else 0)


@main.command()
@click.option("--json", "as_json", is_flag=True, help="The releases as the stable JSON document other checks read.")
@click.option("--line", default=None, help="release/X.Y: list every release cut on that line instead.")
def releases(as_json: bool, line: str | None) -> None:
    """The supported releases of HEAD's tree, oldest first."""
    with _git_errors():
        root = Repository(Path.cwd()).toplevel()
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
