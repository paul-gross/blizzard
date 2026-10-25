"""``blizzard-compat``: hold HEAD's committed surface documents to the supported releases.

    blizzard-compat releases [--json] [--line release/X.Y] [--tag VERSION]   the supported releases themselves
    blizzard-compat [--commits BASE..HEAD] [--line release/X.Y] [--json]   HEAD against them
    blizzard-compat --tag VERSION [--json]   HEAD judged as the tag v<VERSION>; writes compat-breaks.json

HEAD's documents and window are read from the working tree; a release's documents, and the window at
the range's base, only through ``git show``; the allowances from the working tree's
``contracts/compat/allowances.toml``. Nothing at a tag is imported or run. This module is the
composition root: it alone reads the packaged window type and maps it to compat's own ``Window``."""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import click

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.runners.version_window import VersionWindow, VersionWindowError
from blizzard.tools.change_notes.contract import Note
from blizzard.tools.change_notes.tree import CHANGES_DIR, README, read_tree
from blizzard.tools.compat import allowances, gate
from blizzard.tools.compat.git import GitError, Repository
from blizzard.tools.compat.registry import DOCUMENTS
from blizzard.tools.compat.releases import (
    Release,
    Window,
    line_of,
    newest_stable,
    on_line,
    parse_release,
    parse_releases,
    releases_document,
    supported,
)
from blizzard.tools.compat.violation import Violation

_WINDOW_PATH = "src/blizzard/wire/window.toml"
BREAKS_FILE = "compat-breaks.json"


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
    for document in DOCUMENTS:
        path = document.path
        try:
            documents[path] = json.loads((root / path).read_text())
        except OSError as error:
            raise click.ClickException(f"HEAD has no {path}: {error}") from error
    return documents


def _check_release(repo: Repository, rev: str, head: dict[str, dict[str, Any]]) -> list[Outcome]:
    outcomes = []
    for document in DOCUMENTS:
        path = document.path
        text = repo.show(rev, path)
        findings = None if text is None or path not in head else tuple(document.classify(json.loads(text), head[path]))
        outcomes.append(Outcome(path, document.surface, findings))
    return outcomes


def _findings(outcomes: Sequence[Outcome]) -> list[Violation]:
    return [v for outcome in outcomes for v in outcome.findings or ()]


def _state(verdict: gate.Verdict) -> str:
    if verdict.allowance is not None:
        return "allowance"
    if verdict.inherited:
        return "inherited"
    return "allowed" if verdict.passes else "BREAK"


def _print_release(release: str, outcomes: Sequence[Outcome], verdicts: Sequence[gate.Verdict]) -> None:
    click.echo(f"{release}:")
    states = {(v.release, v.violation): _state(v) for v in verdicts}
    because = {(v.release, v.violation): v.allowance for v in verdicts if v.allowance is not None}
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
                if (release, finding) in because:
                    allowance = because[(release, finding)]
                    click.echo(f"      because (line {allowance.line}): {allowance.because}")


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
        "allowance": (
            None
            if verdict.allowance is None
            else {"line": verdict.allowance.line, "because": verdict.allowance.because}
        ),
    }


def _sorted_entries(verdicts: Sequence[gate.Verdict]) -> list[dict[str, Any]]:
    return sorted((_entry(v) for v in verdicts), key=lambda e: (e["release"], e["surface"], e["where"], e["reason"]))


def _read_head_window(root: Path) -> Window:
    try:
        source = root / _WINDOW_PATH
        try:
            text = source.read_text()
        except OSError as error:
            raise VersionWindowError(f"{source}: cannot read the window file: {error}") from error
        return _window(VersionWindow.parse(text, str(source)))
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


def _read_allowances(root: Path) -> list[allowances.Allowance]:
    """The committed allowance entries; a tree without the file records none."""
    source = root / allowances.ALLOWANCES_PATH
    if not source.is_file():
        return []
    try:
        return allowances.parse(source.read_text(), str(source))
    except allowances.AllowanceError as error:
        raise click.ClickException(str(error)) from error


def _stale(
    entries: Sequence[allowances.Allowance],
    in_window: Sequence[Release],
    outcomes: dict[str, list[Outcome]],
) -> list[allowances.Stale]:
    """The entries that fail this run: ``outcomes`` holds every release it judges."""
    return allowances.stale(
        entries,
        in_window={r.tag for r in in_window},
        judged=set(outcomes),
        findings={(tag, f.surface, f.where, f.reason) for tag, o in outcomes.items() for f in _findings(o)},
    )


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
    for document in DOCUMENTS:
        path = document.path
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
    stale: Sequence[allowances.Stale],
) -> None:
    if as_json:
        click.echo(
            json.dumps(
                {
                    "releases": [r.tag for r in releases],
                    "findings": _sorted_entries(verdicts),
                    "window": [{"where": f.where, "reason": f.reason} for f in window_findings],
                    "allowances": [
                        {
                            "line": s.allowance.line,
                            "surface": s.allowance.surface,
                            "where": s.allowance.where,
                            "reason": s.allowance.reason,
                            "problem": s.problem,
                        }
                        for s in stale
                    ],
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
    for entry in stale:
        allowance = entry.allowance
        click.echo(
            f"allowance: [BREAK] line {allowance.line} {allowance.surface} {allowance.where}: {allowance.reason}"
            f" — {entry.problem}"
        )


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
    entries = _read_allowances(root)
    allowed = allowances.admitting(
        entries, _line_key(line_of(line)) if line else None, [r.tag for r in parse_releases(tags)]
    )
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
            allowed=allowed,
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
    stale = _stale(entries, supported(tags, window), outcomes)
    _emit(as_json, releases, outcomes, verdicts, window_findings, stale)
    raise SystemExit(1 if window_findings or stale or any(not v.passes for v in verdicts) else 0)


def _line_key(line: str) -> tuple[int, int]:
    major, minor = line.split(".")
    return int(major), int(minor)


def _candidate(version: str) -> Release:
    candidate = parse_release(f"v{version}")
    if candidate is None:
        raise click.UsageError(f"--tag takes MAJOR.MINOR.PATCH or MAJOR.MINOR.PATCH-rc.N, got {version!r}")
    return candidate


def _without(tags: list[str], candidate: Release | None) -> list[str]:
    """``tags`` less the candidate's own tag, which a run judging that candidate never holds it to."""
    return [t for t in tags if candidate is None or t != candidate.tag]


def _run_tag(version: str, as_json: bool) -> None:
    """Judge HEAD as the tag ``v<version>`` against each supported release, each read over its own ``R..HEAD``.

    A candidate or release above every stable major admits what an open ``master`` admits; any other minor or
    major admits deprecated breaks only; a patch admits nothing. An allowance admits for a candidate on a later line
    than its own, a patch included. Always writes ``compat-breaks.json``."""
    candidate = _candidate(version)
    repo = Repository(Path.cwd())
    root = repo.toplevel()
    repo = Repository(root)
    tags = _without(repo.tags(), candidate)
    window = _read_head_window(root)
    releases = _resolve(None, tags, window)
    stable = newest_stable(tags)
    patch = candidate.patch >= 1
    declarable = not patch and (stable is None or candidate.major > stable.major)
    entries = _read_allowances(root)
    allowed = allowances.admitting(entries, (candidate.major, candidate.minor), [r.tag for r in releases])
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
            allowed=allowed,
        )
    breaks = [
        {key: entry[key] for key in ("release", "surface", "where", "reason", "allowance")}
        for entry in _sorted_entries([v for v in verdicts if v.passes])
    ]
    (root / BREAKS_FILE).write_text(json.dumps({"tag": candidate.tag, "breaks": breaks}, indent=2) + "\n")
    if not as_json:
        click.echo(f"judging {candidate.tag}; supported releases: " + (", ".join(r.tag for r in releases) or "none"))
    stale = _stale(entries, releases, outcomes)
    _emit(as_json, releases, outcomes, verdicts, [], stale)
    raise SystemExit(1 if stale or any(not v.passes for v in verdicts) else 0)


@main.command()
@click.option("--json", "as_json", is_flag=True, help="The releases as the stable JSON document other checks read.")
@click.option("--line", default=None, help="release/X.Y: list every release cut on that line instead.")
@click.option("--tag", default=None, help="VERSION: leave out the tag v<VERSION>, the candidate a tag run judges.")
def releases(as_json: bool, line: str | None, tag: str | None) -> None:
    """The supported releases of HEAD's tree, oldest first."""
    candidate = _candidate(tag) if tag is not None else None
    with _git_errors():
        root = Repository(Path.cwd()).toplevel()
        window = _read_head_window(root)
        listed = _resolve(line, _without(Repository(root).tags(), candidate), window)
    if as_json:
        click.echo(json.dumps(releases_document(window, listed), indent=2))
    elif listed:
        for release in listed:
            click.echo(release.tag)
    else:
        click.echo("no release is supported yet")


if __name__ == "__main__":
    main()
