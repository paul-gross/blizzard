"""Read flag registries from Git release tags and report lifecycle findings."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import click

from blizzard.foundation.flags.registry import FlagRegistry, FlagRegistryError
from blizzard.tools.change_notes.contract import Failure
from blizzard.tools.change_notes.tree import parse_note
from blizzard.tools.compat.git import GitError, Repository
from blizzard.tools.compat.releases import parse_releases
from blizzard.tools.flag_lifecycle_domain import Mode, Snapshot, derive

_REGISTRY = "src/blizzard/foundation/flags/registry.toml"
_UNRELEASED = "changes/unreleased"


class SupportedReleasesError(RuntimeError):
    """``blizzard-compat releases --json`` did not yield the supported releases."""


def supported_tags() -> frozenset[str]:
    """The supported release tags, read through ``blizzard-compat`` rather than recomputed here."""
    command = ["blizzard-compat", "releases", "--json"]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        raise SupportedReleasesError(f"blizzard-compat releases --json could not run: {error}") from error
    if result.returncode != 0:
        raise SupportedReleasesError(
            f"blizzard-compat releases --json failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    try:
        return frozenset(release["tag"] for release in json.loads(result.stdout)["releases"])
    except (ValueError, KeyError, TypeError) as error:
        raise SupportedReleasesError(f"blizzard-compat releases --json gave an unreadable document: {error}") from error


def base_registry(repo: Repository, base: str | None) -> FlagRegistry | None:
    """The registry ``base`` holds, or ``None`` when no base is given or it holds none."""
    text = repo.show(base, _REGISTRY) if base else None
    return FlagRegistry.parse(text, f"{base}:{_REGISTRY}") if text is not None else None


def noted_flags(repo: Repository) -> frozenset[str]:
    """The flag names the notes in HEAD's ``changes/unreleased/`` name."""
    names: set[str] = set()
    for name in repo.names("HEAD", _UNRELEASED):
        if not name.endswith(".md"):
            continue
        path = f"{_UNRELEASED}/{name}"
        note = parse_note(
            repo.show("HEAD", path) or "", path=path, directory="unreleased", note_id=name.removesuffix(".md")
        )
        if not isinstance(note, Failure) and isinstance(note.fields.get("flag"), str):
            names.add(note.fields["flag"])
    return frozenset(names)


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
@click.option(
    "--base", default=None, help="REV: the revision a pull request merges into, against which flips are judged."
)
def main(mode: Mode, base: str | None) -> None:
    """Check release-derived flag lifecycle policy (default: pr)."""
    repo = Repository(Path.cwd())
    try:
        snapshots, head = snapshots_at(repo)
        supported = supported_tags() if snapshots else frozenset()
        rows, findings = derive(
            snapshots, head, mode, supported=supported, base=base_registry(repo, base), noted=noted_flags(repo)
        )
    except (GitError, FlagRegistryError, SupportedReleasesError) as error:
        raise click.ClickException(str(error)) from error
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
