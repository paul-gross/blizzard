"""Recorded allowances: one reviewed finding against one release line, admitted with a written ``because``.

``contracts/compat/allowances.toml`` holds the entries and its README owns their format. An entry admits its exact
finding against whichever release of its line a run judges, and only for a candidate on a later line, since the break
already shipped on that later line. An entry whose line the window no longer supports, or that a run judging its
line's release matches to no finding, is stale and fails the run. Pure over the file's text and the run's findings."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.tools.compat.releases import parse_release

ALLOWANCES_PATH = "contracts/compat/allowances.toml"

_FIELDS = ("line", "surface", "where", "reason", "because")
_LINE = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)")


class AllowanceError(ValueError):
    """The allowance file is malformed; the message names the file and the entry."""


@domain_model
@dataclass(frozen=True)
class Allowance:
    """One entry: the finding it admits against ``line``, and why that line's consumers survive it."""

    line: str
    surface: str
    where: str
    reason: str
    because: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return self.line, self.surface, self.where, self.reason

    def line_order(self) -> tuple[int, int]:
        major, minor = self.line.split(".")
        return int(major), int(minor)


@domain_model
@dataclass(frozen=True)
class Stale:
    """An entry that fails the run, and why."""

    allowance: Allowance
    problem: str


def parse(text: str, source: str) -> list[Allowance]:
    """Every ``[[allowance]]`` entry in ``text``; a missing or extra field, a non-string value, a line that is not
    ``MAJOR.MINOR``, or a repeated finding is refused."""
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise AllowanceError(f"{source}: {error}") from error
    if set(document) - {"allowance"}:
        raise AllowanceError(f"{source}: only [[allowance]] tables are allowed, found {sorted(set(document))}")
    entries: list[Allowance] = []
    for index, table in enumerate(document.get("allowance", []), start=1):
        if set(table) != set(_FIELDS) or not all(isinstance(table[f], str) and table[f].strip() for f in _FIELDS):
            raise AllowanceError(f"{source}: allowance {index} needs exactly {', '.join(_FIELDS)}, each a string")
        entry = Allowance(*(table[f] for f in _FIELDS))
        if not _LINE.fullmatch(entry.line):
            raise AllowanceError(f"{source}: allowance {index}: {entry.line!r} is not a release line, MAJOR.MINOR")
        if any(entry.key == other.key for other in entries):
            raise AllowanceError(f"{source}: allowance {index} repeats an earlier entry's finding")
        entries.append(entry)
    return entries


def _line_of(tag: str) -> str:
    release = parse_release(tag)
    assert release is not None  # every judged or supported release is a release tag
    return release.line


def admitting(
    entries: Sequence[Allowance], candidate_line: tuple[int, int] | None, judged: Collection[str]
) -> dict[tuple[str, str, str, str], Allowance]:
    """By ``(release, surface, where, reason)`` over ``judged`` tags, the entries admitting for ``candidate_line``.

    ``None`` is an open ``master``, above every release's line. A candidate on an entry's own line admits nothing."""
    return {
        (tag, entry.surface, entry.where, entry.reason): entry
        for entry in entries
        if candidate_line is None or entry.line_order() < candidate_line
        for tag in judged
        if _line_of(tag) == entry.line
    }


def stale(
    entries: Sequence[Allowance],
    *,
    in_window: Collection[str],
    judged: Collection[str],
    findings: Collection[tuple[str, str, str, str]],
) -> list[Stale]:
    """The entries that fail the run: a line outside the window, or its in-window release judged with no match."""
    supported = {_line_of(tag): tag for tag in in_window}
    failures = []
    for entry in entries:
        release = supported.get(entry.line)
        if release is None:
            failures.append(Stale(entry, f"line {entry.line} is outside the window"))
        elif release in judged and (release, entry.surface, entry.where, entry.reason) not in findings:
            failures.append(Stale(entry, f"matches no finding against {release}"))
    return failures
