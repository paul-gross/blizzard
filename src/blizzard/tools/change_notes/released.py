"""What a release tag holds, read through git: its notes, its commit block, and where else its notes shipped."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable

from blizzard.tools.change_notes.contract import Failure, Note, check_note
from blizzard.tools.change_notes.tree import CHANGES_DIR, VERSION_DIR, parse_note
from blizzard.tools.compat.git import GitError, Repository
from blizzard.tools.compat.releases import Release

NOTES_SCRIPT = "scripts/release-notes.sh"


class ReleasedNoteInvalid(RuntimeError):
    """A note in a tag's tree breaks the format."""


def notes_at(repo: Repository, tag: str, version: str) -> list[Note]:
    """Every note ``tag`` holds under ``changes/<version>/``, ordered by id."""
    directory = f"{CHANGES_DIR}/{version}"
    notes: list[Note] = []
    for name in repo.names(tag, directory):
        if not name.endswith(".md"):
            continue
        path = f"{directory}/{name}"
        parsed = parse_note(repo.show(tag, path) or "", path=path, directory=version, note_id=name.removesuffix(".md"))
        problems = [parsed] if isinstance(parsed, Failure) else check_note(parsed)
        if problems or isinstance(parsed, Failure):
            raise ReleasedNoteInvalid("\n".join(f"{tag}:{problem}" for problem in problems))
        notes.append(parsed)
    return notes


def also_in(repo: Repository, earlier: Iterable[Release], version: str, ids: Iterable[str]) -> dict[str, list[str]]:
    """For each id, the other versions whose directory an ``earlier`` release holds it under, ascending."""
    wanted = set(ids)
    found: dict[str, set[str]] = {}
    for release in earlier:
        for directory in repo.names(release.tag, CHANGES_DIR):
            if directory == version or not VERSION_DIR.match(directory):
                continue
            for name in repo.names(release.tag, f"{CHANGES_DIR}/{directory}"):
                if name.removesuffix(".md") in wanted:
                    found.setdefault(name.removesuffix(".md"), set()).add(directory)
    return {
        note_id: sorted(versions, key=lambda v: tuple(map(int, v.split(".")))) for note_id, versions in found.items()
    }


def commit_block(repo: Repository, tag: str) -> str:
    """``scripts/release-notes.sh``'s output over the commits since the tag before ``tag``."""
    top = repo.toplevel()
    previous = repo.previous_tag(tag)
    args = ["--range", f"{previous}..{tag}"] if previous else ["--from-stdin"]
    result = subprocess.run(
        ["bash", str(top / NOTES_SCRIPT), *args], cwd=top, input="", capture_output=True, text=True, timeout=60
    )
    if result.returncode != 0:
        raise GitError(f"{NOTES_SCRIPT} failed: {result.stderr.strip()}")
    return result.stdout
