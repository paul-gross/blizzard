"""Walk ``changes/`` and read each file into a ``Note`` or a refusal."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path

from blizzard.hub.documents import YAML_CODEC, ConfigDecodeError
from blizzard.tools.change_notes.contract import Failure, Note

CHANGES_DIR = "changes"
UNRELEASED = "unreleased"
README = "README.md"
KEEP = ".gitkeep"
VERSION_DIR = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_FRONTMATTER = re.compile(r"\A---[ \t]*\n(?:(.*?)\n)?---[ \t]*(?:\n|\Z)(.*)\Z", re.DOTALL)


def _all_str(keys: Iterable[object]) -> bool:
    """Whether every key is a string; the YAML codec's ``dict[str, object]`` does not promise it (``1: x`` loads)."""
    return all(isinstance(key, str) for key in keys)


def parse_note(text: str, *, path: str, directory: str, note_id: str) -> Note | Failure:
    """Split a note file into frontmatter and body; a file that does not parse is a ``Failure``."""
    match = _FRONTMATTER.match(text)
    if match is None:
        return Failure(path, "no-frontmatter", "the file must open with a '---' delimited YAML frontmatter block")
    try:
        loaded = YAML_CODEC.decode((match.group(1) or "").encode("utf-8"))
    except ConfigDecodeError as exc:
        return Failure(path, "bad-frontmatter", f"the frontmatter is not valid YAML: {exc}".splitlines()[0])
    if not _all_str(loaded):
        return Failure(path, "bad-frontmatter", "the frontmatter must be a mapping of field names to values")
    return Note(path=path, directory=directory, id=note_id, fields=loaded, body=match.group(2))


def read_tree(root: Path) -> tuple[list[Note], list[Failure]]:
    """Every note under ``root/changes`` plus the files and directories that break the two shapes."""
    notes: list[Note] = []
    failures: list[Failure] = []
    base = root / CHANGES_DIR
    for entry in sorted(base.iterdir()):
        rel = entry.relative_to(root).as_posix()
        if entry.name == README and entry.is_file():
            continue
        if not entry.is_dir() or not (entry.name == UNRELEASED or VERSION_DIR.match(entry.name)):
            failures.append(
                Failure(rel, "bad-path", "changes/ holds README.md, unreleased/ and X.Y.Z/ directories only")
            )
            continue
        for child in sorted(entry.iterdir()):
            child_rel = child.relative_to(root).as_posix()
            if child.name == KEEP and child.is_file() and entry.name == UNRELEASED:
                continue
            if not child.is_file() or child.suffix != ".md":
                failures.append(Failure(child_rel, "bad-path", f"{entry.name}/ holds <id>.md note files only"))
                continue
            parsed = parse_note(
                child.read_text(encoding="utf-8"), path=child_rel, directory=entry.name, note_id=child.stem
            )
            if isinstance(parsed, Failure):
                failures.append(parsed)
            else:
                notes.append(parsed)
    return notes, failures
