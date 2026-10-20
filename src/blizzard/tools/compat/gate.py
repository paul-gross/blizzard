"""The pass/fail decision over classifier findings, pure over their results.

A finding the base already carried is inherited and never fails. A new one passes against a release
only with a ``!`` commit and either a baseline that marked it deprecated or, on an open ``master``, a
breaking note declaring it; on a release line nothing new passes. The window's own moves are judged
here too: the floor moves only for a break that needs it, and an ``opening`` cannot reopen a major."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.tools.compat.releases import Window
from blizzard.tools.compat.violation import Violation

#: A Conventional Commits breaking marker on a commit subject: ``type!:`` or ``type(scope)!:``.
BREAKING_COMMIT_MARKER = re.compile(r"^\w+(\([^)]*\))?!:")

WINDOW_SURFACE = "window"


@domain_model
@dataclass(frozen=True)
class Verdict:
    """One finding against one release, and whether the change may carry it."""

    release: str
    violation: Violation
    passes: bool
    inherited: bool = False


def is_acknowledged(subjects: Sequence[str]) -> bool:
    return any(BREAKING_COMMIT_MARKER.match(subject) for subject in subjects)


def judge(
    findings: Mapping[str, Sequence[Violation]],
    *,
    acknowledged: bool,
    line: bool,
    declarable: bool = False,
    declared: Collection[tuple[str, str]] = (),
    inherited: Collection[tuple[str, str, str, str]] = (),
) -> list[Verdict]:
    """A verdict per finding per release (``findings`` is keyed by release tag).

    ``inherited`` holds the base's ``(release, surface, where, reason)``; ``declared`` the ``(surface, where)`` an added
    note declares, admitted only when ``declarable``. Under ``line`` no new pair passes."""
    return [
        Verdict(
            release,
            violation,
            (release, *_key(violation)) in inherited
            or (
                not line
                and acknowledged
                and (violation.deprecated or (declarable and (violation.surface, violation.where) in declared))
            ),
            (release, *_key(violation)) in inherited,
        )
        for release, violations in findings.items()
        for violation in violations
    ]


def _key(violation: Violation) -> tuple[str, str, str]:
    return violation.surface, violation.where, violation.reason


def _failing(verdicts: Sequence[Verdict]) -> set[tuple[str, str, str]]:
    """The findings a floor raise would retire: an inherited one still needs it."""
    return {_key(v.violation) for v in verdicts if not v.passes or v.inherited}


def judge_window(
    head: Window,
    base: Window | None,
    *,
    base_verdicts: Sequence[Verdict],
    head_verdicts: Sequence[Verdict],
    newest_stable_major: int | None,
) -> list[Violation]:
    """The findings the window itself earns.

    ``base_verdicts`` are the change's findings judged against the releases ``base``'s floor selects, and
    ``head_verdicts`` against the ones ``head``'s selects. With no ``base`` (the range's base has no window),
    only the ``opening`` is judged."""
    findings: list[Violation] = []
    if head.opening is not None and newest_stable_major is not None and int(head.opening) <= newest_stable_major:
        findings.append(
            Violation(
                WINDOW_SURFACE,
                "opening",
                f"opening {head.opening} is at or below major {newest_stable_major}, the newest stable release's",
            )
        )
    if base is None or head.floor_line == base.floor_line:
        return findings
    if head.opening is not None and base.opening is None:
        return findings  # the change opening master for a major sets the floor beside `opening`
    if head.floor_line < base.floor_line:
        findings.append(Violation(WINDOW_SURFACE, "floor", f"floor lowered from {base.floor} to {head.floor}"))
    elif not (_failing(base_verdicts) - _failing(head_verdicts)):
        findings.append(
            Violation(
                WINDOW_SURFACE, "floor", f"floor raised from {base.floor} to {head.floor} with no break needing it"
            )
        )
    return findings
