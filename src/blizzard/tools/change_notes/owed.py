"""When a note is owed: the judgement over a range's facts, pure.

Four rules: a changed contract document owes an added or edited unreleased note (or a reasoned ``Change-Note: none``
waiver); a ``!`` commit and a newly breaking note owe each other, and the note an ``## Upgrade`` section; a released
note stays put outside the cut that wrote it."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.tools.change_notes.contract import UPGRADE_HEADING, Note, headings
from blizzard.tools.change_notes.tree import CHANGES_DIR, UNRELEASED, VERSION_DIR
from blizzard.tools.compat.gate import BREAKING_COMMIT_MARKER
from blizzard.tools.compat.registry import DOCUMENTS, STORE_SURFACE

#: The CLI contract documents the trigger watches beyond the compat registry.
CLI_DOCUMENTS = ("contracts/cli/hub.json", "contracts/cli/runner.json")
WAIVER = re.compile(r"^none\s+—\s+(\S.*)$")
WAIVER_FORM = "Change-Note: none — <reason>"
UNRELEASED_PREFIX = f"{CHANGES_DIR}/{UNRELEASED}/"


def watched_documents() -> frozenset[str]:
    """Every contract document whose change an operator may notice: the registry's, less the store's."""
    return frozenset(d.path for d in DOCUMENTS if d.surface != STORE_SURFACE) | frozenset(CLI_DOCUMENTS)


@domain_model
@dataclass(frozen=True)
class Change:
    """One path the range changed: its status letter, and the path it was renamed from, if it was."""

    status: str
    path: str
    old_path: str | None = None


@domain_model
@dataclass(frozen=True)
class Commit:
    """One commit of the range: its sha, subject, and the values of its ``Change-Note`` and ``Release-Cut`` trailers."""

    sha: str
    subject: str
    change_notes: tuple[str, ...] = ()
    release_cuts: tuple[str, ...] = ()


@domain_model
@dataclass(frozen=True)
class Facts:
    """What the range holds: its changes and commits, and the notes ``base`` and ``HEAD`` hold by path."""

    changes: tuple[Change, ...]
    commits: tuple[Commit, ...]
    base_notes: Mapping[str, Note]
    head_notes: Mapping[str, Note]


@domain_model
@dataclass(frozen=True)
class Verdict:
    """The failures (rule, paths, remedy) and the honoured waivers and exemptions, as printable lines."""

    failures: tuple[str, ...]
    honoured: tuple[str, ...]


def _released_version(path: str) -> str | None:
    """The version directory a path under ``changes/<X.Y.Z>/`` sits in, else ``None``."""
    parts = path.split("/")
    if len(parts) == 3 and parts[0] == CHANGES_DIR and VERSION_DIR.match(parts[1]) and parts[2].endswith(".md"):
        return parts[1]
    return None


def _is_unreleased_note(path: str) -> bool:
    return path.startswith(UNRELEASED_PREFIX) and path.endswith(".md")


def _added_or_edited(changes: Sequence[Change]) -> list[str]:
    return [c.path for c in changes if c.status in "AMRC" and _is_unreleased_note(c.path)]


def _newly_breaking(facts: Facts) -> list[Note]:
    found: list[Note] = []
    for path, note in sorted(facts.head_notes.items()):
        if not _is_unreleased_note(path) or note.fields.get("breaking") is not True:
            continue
        before = facts.base_notes.get(path)
        if before is None or before.fields.get("breaking") is not True:
            found.append(note)
    return found


def _has_upgrade(note: Note) -> bool:
    return any((level, text) == (2, UPGRADE_HEADING) for level, text, _ in headings(note.body))


def _surface_trigger(facts: Facts) -> tuple[list[str], list[str]]:
    """``(failures, honoured)`` for the surface rule."""
    watched = watched_documents()
    touched = sorted({c.path for c in facts.changes if c.path in watched})
    if not touched or _added_or_edited(facts.changes):
        return [], []
    cuts = sorted({v for c in facts.commits for v in c.release_cuts})
    if cuts:
        return [], [f"surface trigger exempt: the range is the cut of {', '.join(cuts)}"]
    waivers = [(c, value) for c in facts.commits for value in c.change_notes]
    bad = [(c, v) for c, v in waivers if not WAIVER.match(v)]
    good = [(c, WAIVER.match(v)) for c, v in waivers if WAIVER.match(v)]
    failures = [
        f"surface-trigger: {c.sha[:12]} carries a malformed Change-Note trailer {v!r}; the form is `{WAIVER_FORM}`"
        for c, v in bad
    ]
    if good:
        return failures, [
            f"surface trigger waived by {c.sha[:12]}: {m.group(1).strip()}" for c, m in good if m is not None
        ]
    if failures:
        return failures, []
    return [
        f"surface-trigger: the range changes {', '.join(touched)} and adds no note under {UNRELEASED_PREFIX};"
        f" add one, or waive with a trailer `{WAIVER_FORM}`"
    ], []


def _breaking_pair(facts: Facts) -> list[str]:
    marked = [c for c in facts.commits if BREAKING_COMMIT_MARKER.match(c.subject)]
    breaking = _newly_breaking(facts)
    failures: list[str] = []
    if marked and not breaking:
        failures.append(
            f"breaking-pair: {marked[0].sha[:12]} is marked `!` but the range adds no unreleased note with"
            f" `breaking: true`; add one with a `## {UPGRADE_HEADING}` section"
        )
    elif marked and not any(_has_upgrade(n) for n in breaking):
        failures.append(
            f"breaking-pair: {marked[0].sha[:12]} is marked `!` but no breaking note the range adds has a"
            f" `## {UPGRADE_HEADING}` section; add it to {breaking[0].path}"
        )
    if breaking and not marked:
        failures.append(
            f"breaking-pair: the range adds the breaking note {', '.join(n.path for n in breaking)} but no commit"
            " subject carries the `!` marker; mark the breaking commit `type!:`"
        )
    return failures


def _released_stay_put(facts: Facts) -> list[str]:
    exempt = {v for c in facts.commits for v in c.release_cuts}
    failures: set[str] = set()

    def refuse(path: str, what: str) -> None:
        failures.add(
            f"released-note: {path} {what}; a note under {CHANGES_DIR}/<X.Y.Z>/ stays as released"
            " (only a body edit, or the cut of its own version, may touch it)"
        )

    for change in facts.changes:
        version = _released_version(change.path)
        if change.old_path is not None and (old := _released_version(change.old_path)) and old not in exempt:
            refuse(change.old_path, "is renamed away")
        if version is None or version in exempt:
            continue
        if change.status == "M":
            before, after = facts.base_notes.get(change.path), facts.head_notes.get(change.path)
            if before is None or after is None or before.fields != after.fields:
                refuse(change.path, "has its frontmatter changed")
        else:
            refuse(change.path, {"D": "is deleted", "R": "is renamed in"}.get(change.status, "is added"))
    return sorted(failures)


def judge(facts: Facts) -> Verdict:
    """What the range owes and does not add, and what it was excused from."""
    failures, honoured = _surface_trigger(facts)
    failures += _breaking_pair(facts)
    failures += _released_stay_put(facts)
    return Verdict(tuple(failures), tuple(honoured))
