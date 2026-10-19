"""Derive flag lifetimes from release-tag registry snapshots and check the current tree."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import click

from blizzard.foundation.flags.registry import FlagRegistry, FlagRegistryError
from blizzard.foundation.roles import domain_model
from blizzard.tools.compat.git import GitError, Repository
from blizzard.tools.compat.releases import Release, parse_releases

_REGISTRY = "src/blizzard/foundation/flags/registry.toml"
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
    severity: Literal["FAIL", "WARN"]
    message: str


def derive(snapshots: list[Snapshot], head: FlagRegistry, mode: Mode) -> tuple[list[Row], list[Finding]]:
    """Decide lifecycle policy over ordered releases and the current tree, without Git I/O."""
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
        if off_for >= 12:
            findings.append(Finding(name, "off-twelve-releases", "WARN", f"off in {off_for} tagged releases"))
    return rows, findings


def snapshots_at(repo: Repository) -> tuple[list[Snapshot], FlagRegistry]:
    head_text = repo.show("HEAD", _REGISTRY)
    if head_text is None:
        raise FlagRegistryError(f"HEAD:{_REGISTRY}: missing flag registry")
    head = FlagRegistry.parse(head_text, f"HEAD:{_REGISTRY}")
    releases = []
    for release in parse_releases(repo.tags()):
        text = repo.show(release.tag, _REGISTRY)
        if text is not None:
            releases.append(Snapshot(release, FlagRegistry.parse(text, f"{release.tag}:{_REGISTRY}")))
    return releases, head


@click.command()
@click.argument("mode", type=click.Choice(["pr", "cut", "tag"]), default="pr")
def main(mode: Mode) -> None:
    """Check release-derived flag lifecycle policy (default: pr)."""
    try:
        snapshots, head = snapshots_at(Repository(Path.cwd()))
    except (GitError, FlagRegistryError) as error:
        raise click.ClickException(str(error)) from error
    rows, findings = derive(snapshots, head, mode)
    for row in rows:
        click.echo(
            f"{row.name}: born in={row.born_in or '-'}; on in={row.on_in or '-'}; "
            f"due={'yes' if row.due else 'no'}; retired in={row.retired_in or '-'}; off for={row.off_for}"
        )
    for finding in findings:
        click.echo(f"{finding.severity} [{finding.rule}] {finding.name}: {finding.message}")
    if not snapshots:
        click.echo("No tagged registry snapshots; HEAD registry structure valid.")
    raise SystemExit(1 if any(f.severity == "FAIL" for f in findings) else 0)


if __name__ == "__main__":
    main()
