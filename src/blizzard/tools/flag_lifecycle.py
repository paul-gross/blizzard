"""Read flag registries from Git release tags and report lifecycle findings."""

from __future__ import annotations

from pathlib import Path

import click

from blizzard.foundation.flags.registry import FlagRegistry, FlagRegistryError
from blizzard.tools.compat.git import GitError, Repository
from blizzard.tools.compat.releases import parse_releases
from blizzard.tools.flag_lifecycle_domain import Mode, Snapshot, derive

_REGISTRY = "src/blizzard/foundation/flags/registry.toml"


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
