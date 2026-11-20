"""Conservative preparation and admission of a linear automatic rebase."""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class TreeEntry:
    """A complete leaf entry; content is absent only for a gitlink."""

    path: str
    mode: str
    oid: str
    content: bytes | None


@domain_model
@dataclass(frozen=True)
class Commit:
    oid: str
    parents: tuple[str, ...]
    tree: tuple[TreeEntry, ...]
    author: str
    message: str


@domain_model
@dataclass(frozen=True)
class ExpectedEntry:
    mode: str
    # None means the object id is not known until the textual replay is written.
    oid: str | None
    content: bytes | None


@domain_model
@dataclass(frozen=True)
class PreparedCommit:
    author: str
    message: str
    tree: tuple[tuple[str, ExpectedEntry], ...]


@domain_model
@dataclass(frozen=True)
class RewritePlan:
    base_oid: str
    commits: tuple[PreparedCommit, ...]


def _tree(entries: tuple[TreeEntry, ...]) -> dict[str, ExpectedEntry] | None:
    result: dict[str, ExpectedEntry] = {}
    for entry in entries:
        if (
            not entry.path
            or any(part in {"", ".", ".."} for part in entry.path.split("/"))
            or entry.path in result
            or not entry.oid
            or entry.mode not in {"100644", "100755", "120000", "160000"}
            or (entry.content is None) != (entry.mode == "160000")
        ):
            return None
        result[entry.path] = ExpectedEntry(entry.mode, entry.oid, entry.content)
    # A tree cannot contain both a leaf and one of its descendants.
    if any("/".join(path.split("/")[:i]) in result for path in result for i in range(1, len(path.split("/")))):
        return None
    return result


def _edits(old: list[bytes], new: list[bytes]) -> list[tuple[int, int, list[bytes]]]:
    return [
        (a, b, new[c:d])
        for tag, a, b, c, d in SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
        if tag != "equal"
    ]


def _replay_text(old: bytes, source: bytes, base: bytes) -> bytes | None:
    try:
        for content in (old, source, base):
            content.decode("utf-8")
            if b"\0" in content:
                return None
    except UnicodeDecodeError:
        return None
    original, desired, current = (content.splitlines(keepends=True) for content in (old, source, base))
    # Repeated lines allow more than one alignment of the same change.
    if any(len(lines) != len(set(lines)) for lines in (original, desired, current)):
        return None
    work, upstream = _edits(original, desired), _edits(original, current)
    moved: list[tuple[int, int, list[bytes]]] = []
    for start, end, lines in work:
        shift = 0
        for other_start, other_end, other_lines in upstream:
            # Abutting edits and insertions at the same boundary are ambiguous.
            if other_end < start:
                shift += len(other_lines) - (other_end - other_start)
            elif not end < other_start:
                return None
        moved.append((start + shift, end + shift, lines))
    for start, end, lines in reversed(moved):
        current[start:end] = lines
    return b"".join(current)


def prepare(source_base: Commit, source_commits: tuple[Commit, ...], base: Commit) -> RewritePlan | None:
    """Prepare before mutation; incomplete or ambiguous history has no plan."""
    previous, target = _tree(source_base.tree), _tree(base.tree)
    if previous is None or target is None or not source_base.oid or not base.oid or not source_commits:
        return None
    parent = source_base.oid
    seen = {parent}
    prepared: list[PreparedCommit] = []
    for commit in source_commits:
        current = _tree(commit.tree)
        if current is None or not commit.oid or commit.oid in seen or commit.parents != (parent,):
            return None
        changes = {path for path in previous.keys() | current.keys() if previous.get(path) != current.get(path)}
        if not changes:
            return None
        result = target.copy()
        for path in changes:
            before, after, onto = previous.get(path), current.get(path), target.get(path)
            if onto == before:
                if after is None:
                    result.pop(path, None)
                else:
                    result[path] = after
            elif (
                before is not None
                and after is not None
                and onto is not None
                and before.mode == after.mode == onto.mode
                and before.mode in {"100644", "100755"}
                and before.content is not None
                and after.content is not None
                and onto.content is not None
            ):
                content = _replay_text(before.content, after.content, onto.content)
                if content is None or content == onto.content:
                    return None
                result[path] = ExpectedEntry(after.mode, None, content)
            else:
                return None
        if result == target:
            return None
        # Reject file/directory collisions introduced by replay.
        if any("/".join(path.split("/")[:i]) in result for path in result for i in range(1, len(path.split("/")))):
            return None
        prepared.append(PreparedCommit(commit.author, commit.message, tuple(sorted(result.items()))))
        previous, target, parent = current, result, commit.oid
        seen.add(parent)
    return RewritePlan(base.oid, tuple(prepared))


def proves(plan: RewritePlan, rewritten: tuple[Commit, ...]) -> bool:
    """Require the entire linear chain and every full tree transition to match."""
    if not plan.base_oid or not plan.commits or len(rewritten) != len(plan.commits):
        return False
    parent = plan.base_oid
    seen = {parent}
    for expected, commit in zip(plan.commits, rewritten, strict=True):
        actual = _tree(commit.tree)
        if (
            actual is None
            or not commit.oid
            or commit.oid in seen
            or commit.parents != (parent,)
            or (commit.author, commit.message) != (expected.author, expected.message)
        ):
            return False
        wanted = dict(expected.tree)
        if actual.keys() != wanted.keys():
            return False
        for path, entry in actual.items():
            expectation = wanted[path]
            if (
                entry.mode != expectation.mode
                or entry.content != expectation.content
                or (expectation.oid is not None and entry.oid != expectation.oid)
            ):
                return False
        parent = commit.oid
        seen.add(parent)
    return True
