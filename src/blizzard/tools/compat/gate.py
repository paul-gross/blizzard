"""The pass/fail decision over classifier findings, pure over their results.

On ``master`` before 1.0 a finding passes against a release only when that release's baseline
marked the removed surface deprecated and the change carries a ``!`` commit. On a release line
nothing passes. The window's own moves are judged here too: the floor moves only for a break that
needs it, and an ``opening`` cannot reopen a shipped major."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
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


def is_acknowledged(subjects: Sequence[str]) -> bool:
    return any(BREAKING_COMMIT_MARKER.match(subject) for subject in subjects)


def judge(findings: Mapping[str, Sequence[Violation]], *, acknowledged: bool, line: bool) -> list[Verdict]:
    """A verdict per finding per release (``findings`` is keyed by release tag).

    A pair passes only if the finding is deprecated against that release and the change acknowledges a
    break. Under ``line`` nothing passes: a patch adds or it is not a patch."""
    return [
        Verdict(release, violation, not line and acknowledged and violation.deprecated)
        for release, violations in findings.items()
        for violation in violations
    ]


def _key(violation: Violation) -> tuple[str, str, str]:
    return violation.surface, violation.where, violation.reason


def _failing(verdicts: Sequence[Verdict]) -> set[tuple[str, str, str]]:
    return {_key(v.violation) for v in verdicts if not v.passes}


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
