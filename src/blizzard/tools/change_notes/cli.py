"""``blizzard-change-notes`` — the change-note tooling's command group."""

from __future__ import annotations

from pathlib import Path

import click

from blizzard.tools.change_notes.cut import CutRefused, cut
from blizzard.tools.change_notes.released import ReleasedNoteInvalid, also_in, commit_block, notes_at
from blizzard.tools.change_notes.render import render
from blizzard.tools.change_notes.tree import CHANGES_DIR
from blizzard.tools.change_notes.validate import validate
from blizzard.tools.compat.git import GitError, Repository
from blizzard.tools.compat.releases import parse_release, parse_releases


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
        click.echo(f"nothing to cut: {CHANGES_DIR}/unreleased/ holds no notes")
    for note_id in report.notes:
        click.echo(f"moved {note_id} to {CHANGES_DIR}/{version}/")
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
        text = render(tag, notes, also_in(repo, earlier, version, (n.id for n in notes)), commit_block(repo, tag))
    except (GitError, ReleasedNoteInvalid) as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc
    click.echo(text, nl=False)


if __name__ == "__main__":
    main()
