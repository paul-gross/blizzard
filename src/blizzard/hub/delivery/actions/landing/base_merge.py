"""Whether a merge commit adds exactly the base branch's change — the proof behind the head gate,
worked from the forge's comparisons alone. A file both sides changed
is admitted only when the merge's change blocks equal the base's blocks translated into the
feature side's line coordinates; unprovable is refused
(``blizzard-context:/domain/artifacts/model.md``)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from blizzard.foundation.roles import domain_model
from blizzard.hub.forge.landing import Comparison, FileChange

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@domain_model
@dataclass(frozen=True)
class Block:
    """One run of changed lines: ``start`` is the old-file line the run begins at (for a pure
    insertion, the old line it precedes), ``removed`` how many old lines it spans, and ``lines``
    its ``+``/``-``/``\\`` lines verbatim — the "No newline" marker is content."""

    start: int
    removed: int
    added: int
    lines: tuple[str, ...]


def files_by_name(comparison: Comparison) -> dict[str, FileChange] | None:
    """A comparison's files by path, or ``None`` when the forge cut the list short."""
    if comparison.files_truncated:
        return None
    return {f.path: f for f in comparison.files}


def parse_blocks(file: FileChange) -> list[Block] | None:
    """A file's change blocks, or ``None`` when its patch cannot be trusted complete: no
    patch, a hunk whose header counts disagree with its body, or ``+``/``-`` totals that
    disagree with the file's ``additions``/``deletions``."""
    patch, adds, dels = file.patch, file.additions, file.deletions
    if patch is None or not _is_count(adds) or not _is_count(dels):
        return None
    lines = patch.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    blocks: list[Block] = []
    total_added = total_removed = 0
    i = 0
    while i < len(lines):
        header = _HUNK.match(lines[i])
        if header is None:
            return None
        old_start, old_count = int(header[1]), 1 if header[2] is None else int(header[2])
        new_count = 1 if header[4] is None else int(header[4])
        cursor = old_start + 1 if old_count == 0 else old_start
        i += 1
        seen_old = seen_new = 0
        run: list[str] = []
        run_start = cursor
        removed = added = 0
        while i < len(lines) and not lines[i].startswith("@@"):
            line = lines[i]
            kind = line[:1]
            if kind == "\\":
                if run:
                    run.append(line)
            elif kind in {"+", "-"}:
                if not run:
                    run_start = cursor
                run.append(line)
                if kind == "-":
                    removed += 1
                    seen_old += 1
                    cursor += 1
                elif kind == "+":
                    added += 1
                    seen_new += 1
            elif kind == " ":
                if run:
                    blocks.append(Block(run_start, removed, added, tuple(run)))
                    total_added += added
                    total_removed += removed
                    run, removed, added = [], 0, 0
                seen_old += 1
                seen_new += 1
                cursor += 1
            else:
                return None
            i += 1
        if run:
            blocks.append(Block(run_start, removed, added, tuple(run)))
            total_added += added
            total_removed += removed
        if (seen_old, seen_new) != (old_count, new_count):
            return None
    if (total_added, total_removed) != (adds, dels):
        return None
    return blocks


def _is_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _translate(base_blocks: list[Block], feature_blocks: list[Block]) -> list[Block] | None:
    """``base_blocks`` (in merge-base coordinates) moved into the feature side's coordinates
    through ``feature_blocks``; ``None`` when any block overlaps or abuts a feature block — git
    conflicts there, so a merge over it was resolved by hand."""
    moved: list[Block] = []
    for block in base_blocks:
        shift = 0
        for other in feature_blocks:
            if other.start + other.removed < block.start:
                shift += other.added - other.removed
            elif not block.start + block.removed < other.start:
                return None
        moved.append(Block(block.start + shift, block.removed, block.added, block.lines))
    return moved


def _same_blob(merged: FileChange, base_side: FileChange) -> bool:
    return merged.blob is not None and merged.blob == base_side.blob and merged.previous_path == base_side.previous_path


def _same_changes(merged: FileChange, base_side: FileChange, feature_side: FileChange) -> bool:
    if any(f.previous_path is not None for f in (merged, base_side, feature_side)):
        return False
    got, want, feature = parse_blocks(merged), parse_blocks(base_side), parse_blocks(feature_side)
    if got is None or want is None or feature is None:
        return False
    moved = _translate(want, feature)
    return moved is not None and moved == got


def contributes_only_base_change(merged: Comparison, base_side: Comparison, feature_side: Comparison) -> bool:
    """Whether the merge adds exactly the base side's change, given three compares:
    ``merged`` (``first...M``), ``base_side`` (``first...second``, so merge base → ``second``),
    and ``feature_side`` (merge base → ``first``). Unprovable is ``False``."""
    got, want, feature = files_by_name(merged), files_by_name(base_side), files_by_name(feature_side)
    if got is None or want is None or feature is None or got.keys() != want.keys():
        return False
    touched = set(feature) | {f.previous_path for f in feature.values() if f.previous_path is not None}
    for name, file in got.items():
        other = want[name]
        renamed_from = {f.previous_path for f in (file, other)}
        if name in touched or renamed_from & feature.keys():
            if name not in feature or not _same_changes(file, other, feature[name]):
                return False
        elif not _same_blob(file, other):
            return False
    return True
