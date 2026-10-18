"""``blizzard-change-notes`` — the change-note tooling's command group."""

from __future__ import annotations

from pathlib import Path

import click

from blizzard.tools.change_notes.cut import CutRefused, cut
from blizzard.tools.change_notes.flags import turned_on
from blizzard.tools.change_notes.guide import due_without_removal, guide
from blizzard.tools.change_notes.released import ReleasedNoteInvalid, also_in, commit_block, notes_at
from blizzard.tools.change_notes.render import render
from blizzard.tools.change_notes.schedule import latest_released_schedule, release_line
from blizzard.tools.change_notes.tree import CHANGES_DIR, VERSION_DIR, read_tree
from blizzard.tools.change_notes.validate import validate
from blizzard.tools.compat.git import GitError, Repository
from blizzard.tools.compat.releases import parse_release, parse_releases


def _tag_notes(repo: Repository, tag: str) -> list:
    directories = (d for d in repo.names(tag, CHANGES_DIR) if VERSION_DIR.fullmatch(d))
    return [note for directory in directories for note in notes_at(repo, tag, directory)]


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


@main.command(name="cut")
@click.argument("version")
def cut_command(version: str) -> None:
    """Move every unreleased note, and the upgrade boundaries they name, under VERSION (X.Y.Z)."""
    root = Path.cwd()
    if not (root / CHANGES_DIR).is_dir():
        raise click.UsageError(f"no {CHANGES_DIR}/ directory under {root}; run from the repository root")
    try:
        report = cut(root, version)
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


@main.command(name="render")
@click.argument("tag")
def render_command(tag: str) -> None:
    """Print the Markdown release notes TAG's tree publishes: its notes by section, then its commits."""
    release = parse_release(tag)
    if release is None:
        raise click.UsageError(f"{tag!r} is not a release tag; expected vX.Y.Z or vX.Y.Z-rc.N")
    version = f"{release.major}.{release.minor}.{release.patch}"
    repo = Repository(Path.cwd())
    try:
        earlier = [r for r in parse_releases(repo.tags()) if r.order() < release.order()]
        notes = notes_at(repo, tag, version)
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
        )
    except (GitError, ReleasedNoteInvalid) as exc:
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
    if due := due_without_removal(notes, line, prospective=True):
        click.echo(f"deprecations due in {line} without a retiring removal: {', '.join(due)}", err=True)
        raise SystemExit(1)
    click.echo(guide(notes, line, prospective=True), nl=False)


if __name__ == "__main__":
    main()
