"""Flag lifecycle facts and policy derived from parsed release snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from blizzard.foundation.flags.registry import FlagRegistry
from blizzard.foundation.roles import domain_model
from blizzard.tools.compat.releases import Release

type Mode = Literal["pr", "cut", "tag"]


@domain_model
@dataclass(frozen=True)
class Snapshot:
    release: Release
    registry: FlagRegistry


@domain_model
@dataclass(frozen=True)
class Row:
    name: str
    born_in: str | None
    on_in: str | None
    due: bool
    retired_in: str | None
    off_for: int


@domain_model
@dataclass(frozen=True)
class Finding:
    name: str
    rule: str
    severity: Literal["FAIL", "WARN", "NOTE"]
    message: str


def derive(
    snapshots: list[Snapshot],
    head: FlagRegistry,
    mode: Mode,
    *,
    supported: frozenset[str] = frozenset(),
    base: FlagRegistry | None = None,
    noted: frozenset[str] = frozenset(),
) -> tuple[list[Row], list[Finding]]:
    """Decide lifecycle policy over ordered releases and the current tree, without Git I/O.

    ``supported`` is the tags of the supported releases, ``base`` the registry a pull request merges into, and
    ``noted`` the flag names an unreleased note at HEAD names.
    """
    rows: list[Row] = []
    findings: list[Finding] = []
    names = sorted(
        set(head.live) | set(head.retired) | {n for s in snapshots for n in (*s.registry.live, *s.registry.retired)}
    )
    for name in names:
        born = next((s.release for s in snapshots if name in s.registry.live), None)
        on = next(
            (
                s.release
                for s in snapshots
                if s.release.is_stable() and name in s.registry.live and s.registry.live[name].default
            ),
            None,
        )
        retired = next((s.release for s in snapshots if name in s.registry.retired), None)
        due = on is not None and any(
            s.release.is_stable() and s.release.order() > on.order() and name in s.registry.live for s in snapshots
        )
        off_for = sum(
            born is not None
            and s.release.order() >= born.order()
            and name in s.registry.live
            and not s.registry.live[name].default
            for s in snapshots
        )
        rows.append(
            Row(
                name, born.tag if born else None, on.tag if on else None, due, retired.tag if retired else None, off_for
            )
        )

        if (
            mode == "pr"
            and base is not None
            and name in base.live
            and not base.live[name].default
            and name in head.live
            and head.live[name].default
            and name not in noted
        ):
            findings.append(Finding(name, "flip-without-note", "FAIL", "turned on with no unreleased note naming it"))
        if not snapshots:
            continue
        states = [(s.release.tag, s.registry, s.release.is_stable()) for s in snapshots] + [("HEAD", head, False)]
        seen_live = False
        seen_retired = False
        stable_on: str | None = None
        for label, registry, stable in states:
            live = registry.live.get(name)
            if live is not None:
                if not seen_live and live.default:
                    findings.append(Finding(name, "initial-on", "FAIL", f"created on at {label}"))
                if seen_retired:
                    findings.append(Finding(name, "retired-name-reuse", "FAIL", f"live after retirement at {label}"))
                if stable_on is not None and not live.default:
                    findings.append(
                        Finding(name, "post-stable-reversal", "FAIL", f"off at {label} after {stable_on} carried on")
                    )
                seen_live = True
                if stable and live.default:
                    stable_on = label
            if name in registry.retired:
                seen_retired = True
        if name in head.live and head.live[name].default and on is not None:
            if due:
                findings.append(
                    Finding(
                        name,
                        "due-live",
                        "WARN" if mode == "pr" else "FAIL",
                        f"still live after a later stable release than {on.tag}",
                    )
                )
            elif mode == "pr":
                findings.append(Finding(name, "on-live", "WARN", f"on since {on.tag}; retire after promotion"))
        held_live = [s.release.tag for s in snapshots if s.release.tag in supported and name in s.registry.live]
        if name in head.retired and not held_live:
            findings.append(Finding(name, "tombstone-removable", "NOTE", "no supported release holds it live; may go"))
        if held_live and name not in head.live and name not in head.retired:
            findings.append(
                Finding(name, "tombstone-removed", "FAIL", f"gone from HEAD while {held_live[0]} holds it live")
            )
        if name in head.live and off_for >= 12:
            findings.append(Finding(name, "off-twelve-releases", "WARN", f"off in {off_for} tagged releases"))
    return rows, findings
