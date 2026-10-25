"""``blizzard-change-notes`` — the change-note tooling's command group."""

from __future__ import annotations

from pathlib import Path

import click

from blizzard.tools.change_notes import owed as owed_rules
from blizzard.tools.change_notes.breaks import BreaksFileInvalid, compat_breaks, cross_check, read_breaks, unnamed_lines
from blizzard.tools.change_notes.contract import Failure, Note
from blizzard.tools.change_notes.cut import CutRefused, cut
from blizzard.tools.change_notes.flags import turned_on, working_registry
from blizzard.tools.change_notes.guide import guide
from blizzard.tools.change_notes.released import ReleasedNoteInvalid, also_in, commit_block, notes_at
from blizzard.tools.change_notes.render import render
from blizzard.tools.change_notes.schedule import due_without_removal, latest_released_schedule, release_line
from blizzard.tools.change_notes.tree import CHANGES_DIR, UNRELEASED, VERSION_DIR, parse_note, read_tree
from blizzard.tools.change_notes.validate import validate
from blizzard.tools.compat.git import GitError, Repository
from blizzard.tools.compat.reader import CompatError, supported_tags
from blizzard.tools.compat.releases import parse_release, parse_releases


def _tag_notes(repo: Repository, tag: str) -> list:
    directories = (d for d in repo.names(tag, CHANGES_DIR) if VERSION_DIR.fullmatch(d))
    return [note for directory in directories for note in notes_at(repo, tag, directory)]


def _notes_at(repo: Repository, rev: str, paths: list[str]) -> dict[str, Note]:
    """The parseable notes among ``paths`` as ``rev`` holds them; the working tree is never read."""
    notes: dict[str, Note] = {}
    for path in paths:
        text = repo.show(rev, path)
        if text is None:
            continue
        directory, name = path.rsplit("/", 2)[-2:]
        parsed = parse_note(text, path=path, directory=directory, note_id=name.removesuffix(".md"))
        if not isinstance(parsed, Failure):
            notes[path] = parsed
    return notes


def _range_facts(repo: Repository, against: str) -> owed_rules.Facts:
    base = repo.merge_base(against, "HEAD")
    span = f"{base}..HEAD"
    waivers = dict(repo.trailers(span, "Change-Note"))
    cuts = dict(repo.trailers(span, "Release-Cut"))
    commits = tuple(
        owed_rules.Commit(sha, subject, tuple(waivers.get(sha, ())), tuple(cuts.get(sha, ())))
        for sha, subject in repo.commits(span)
    )
    changes = tuple(owed_rules.Change(status, path, old) for status, path, old in repo.changed(base, "HEAD"))
    paths = sorted(
        {
            p
            for c in changes
            for p in (c.path, c.old_path)
            if p and p.startswith(f"{CHANGES_DIR}/") and p.endswith(".md")
        }
    )
    return owed_rules.Facts(changes, commits, _notes_at(repo, base, paths), _notes_at(repo, "HEAD", paths))


@click.group()
def main() -> None:
    """Tooling for the change notes under ``changes/``."""


@main.command()
def check() -> None:
    """Fail on any change note, or file under changes/, that breaks the format."""
    root = Path.cwd()
    if not (root / CHANGES_DIR).is_dir():
        raise click.UsageError(f"no {CHANGES_DIR}/ directory under {root}; run from the repository root")
    failures = validate(root)
    for failure in failures:
        click.echo(failure)
    if failures:
        click.echo(f"{len(failures)} change-note failure(s)", err=True)
        raise SystemExit(1)
    click.echo("change notes ok")


@main.command()
@click.option(
    "--against", required=True, help="The ref the change is judged against: the range is merge-base(HEAD, ref)..HEAD."
)
def owed(against: str) -> None:
    """Fail when the commits of the range owe a change note they do not add."""
    try:
        repo = Repository(Path.cwd())
        verdict = owed_rules.judge(_range_facts(Repository(repo.toplevel()), against))
    except GitError as exc:
        raise click.UsageError(str(exc)) from exc
    for line in verdict.honoured:
        click.echo(line)
    for line in verdict.failures:
        click.echo(line)
    if verdict.failures:
        click.echo(f"{len(verdict.failures)} change-note obligation(s) unmet", err=True)
        raise SystemExit(1)
    click.echo("no change note owed beyond what the range adds")


@main.command(name="cut")
@click.argument("version")
def cut_command(version: str) -> None:
    """Move every unreleased note, and the upgrade boundaries they name, under VERSION (X.Y.Z)."""
    root = Path.cwd()
    if not (root / CHANGES_DIR).is_dir():
        raise click.UsageError(f"no {CHANGES_DIR}/ directory under {root}; run from the repository root")
    try:
        report = cut(root, version, lambda: compat_breaks(root, version))
    except CutRefused as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc
    if not report.notes:
        click.echo(
            "nothing to cut: all unreleased notes are held" if report.held else "nothing to cut: no unreleased notes"
        )
    for note_id in report.notes:
        click.echo(f"moved {note_id} to {CHANGES_DIR}/{version}/")
    for note_id in report.held:
        click.echo(f"held {note_id} in {CHANGES_DIR}/unreleased/: its flag is off")
    for anchor in report.boundaries:
        click.echo(f"moved upgrade boundary {anchor}")
    for anchor in report.left:
        click.echo(f"left upgrade boundary {anchor} in place: it is not staged for the next release")
    for surface, where in report.unrecorded:
        click.echo(f"declared break {surface} {where} is recorded by no compat break")


@main.command(name="render")
@click.argument("tag")
@click.option(
    "--breaks",
    "breaks_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="compat-breaks.json: fail on a recorded break no note names, and list the recorded breaks.",
)
def render_command(tag: str, breaks_path: Path | None) -> None:
    """Print the Markdown release notes TAG's tree publishes: its notes by section, then its commits."""
    release = parse_release(tag)
    if release is None:
        raise click.UsageError(f"{tag!r} is not a release tag; expected vX.Y.Z or vX.Y.Z-rc.N")
    version = f"{release.major}.{release.minor}.{release.patch}"
    repo = Repository(Path.cwd())
    try:
        notes = notes_at(repo, tag, version)
        supported = supported_tags(version) if notes else frozenset()
        earlier = [r for r in parse_releases(repo.tags()) if r.order() < release.order() and r.tag in supported]
        named = ()
        if breaks_path is not None:
            recorded_tag, entries = read_breaks(breaks_path)
            if recorded_tag != tag:
                raise ReleasedNoteInvalid(f"{breaks_path} judged {recorded_tag}, not {tag}")
            check = cross_check(entries, notes)
            if check.unnamed:
                raise ReleasedNoteInvalid(unnamed_lines(check))
            named = check.named
            for surface, where in check.unrecorded:
                click.echo(f"declared break {surface} {where} is recorded by no compat break", err=True)
        history = _tag_notes(repo, tag) if notes else []
        opening = release.patch == 0 and (release.major != 0 or release.minor != 0)
        introduction = ""
        if opening and notes:
            line = release_line(version)
            if release.major == 0 and line not in latest_released_schedule(history).values():
                opening = False
            if opening and (due := due_without_removal(history, line)):
                raise ReleasedNoteInvalid(f"deprecations due in {line} without a retiring removal: {', '.join(due)}")
            if opening:
                introduction = guide(history, line, tag=tag) or f"## Upgrading to {line}\n\n"
        text = introduction + render(
            tag,
            notes,
            also_in(repo, earlier, version, (n.id for n in notes)),
            commit_block(repo, tag),
            turned_on(repo, tag),
            latest_released_schedule(history),
            named,
        )
    except (GitError, ReleasedNoteInvalid, BreaksFileInvalid, CompatError) as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc
    click.echo(text, nl=False)


@main.command(name="guide")
@click.argument("line")
def guide_command(line: str) -> None:
    """Print the upgrade guide for a release line (a major, or a pre-1.0 minor)."""
    import re

    if not re.fullmatch(r"(?:[1-9][0-9]*|0\.[1-9][0-9]*)", line):
        raise click.UsageError(f"{line!r} is not a release line; expected X or 0.Y")
    root = Path.cwd()
    if not (root / CHANGES_DIR).is_dir():
        raise click.UsageError(f"no {CHANGES_DIR}/ directory under {root}; run from the repository root")
    failures = validate(root)
    if failures:
        click.echo("\n".join(map(str, failures)), err=True)
        raise SystemExit(1)
    notes, _ = read_tree(root)
    defaults = working_registry(root)
    eligible = [
        n
        for n in notes
        if n.directory != UNRELEASED or not n.fields.get("flag") or defaults[n.fields["flag"]]["default"] == "on"
    ]
    if due := due_without_removal(eligible, line, prospective=True):
        click.echo(f"deprecations due in {line} without a retiring removal: {', '.join(due)}", err=True)
        raise SystemExit(1)
    click.echo(guide(eligible, line, prospective=True), nl=False)


if __name__ == "__main__":
    main()
