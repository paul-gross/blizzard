"""Export the hub and runner OpenAPI specs to a stable path.

The single source of both specs (``bzh:generated-client``): each app is built from
throwaway config — no store, no server — and dumped as deterministic, sorted JSON, so a
drift check over the committed output is stable."""

from __future__ import annotations

import json
from pathlib import Path

import click

from blizzard.hub.app import create_app_for_export as build_hub_app
from blizzard.runner.app import create_app_for_export as build_runner_app

SPECS = (
    ("hub", build_hub_app),
    ("runner", build_runner_app),
)


def export(out_dir: Path) -> list[Path]:
    """Write both specs into ``out_dir``; return the written paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, build in SPECS:
        spec = build().openapi()
        path = out_dir / f"{name}.openapi.json"
        path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
        written.append(path)
    return written


@click.command()
@click.option(
    "--out-dir",
    default="openapi",
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to write the OpenAPI JSON specs into.",
)
def main(out_dir: Path) -> None:
    """Dump the hub and runner OpenAPI specs for client generation and drift checks."""
    for path in export(out_dir):
        click.echo(f"wrote {path}")


if __name__ == "__main__":
    main()
