"""Export the committed contract documents to a stable path.

Today that is each store's schema document (``contracts/store/<store>.json``), built from
its ``schema.py`` and its migration tree's head revision alone — no store is opened — and
dumped as deterministic, sorted JSON, so a drift check over the committed output is stable."""

from __future__ import annotations

import json
from pathlib import Path

import click
from sqlalchemy import MetaData

from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.store import MIGRATIONS_DIR as HUB_MIGRATIONS_DIR
from blizzard.hub.store.schema import metadata as hub_metadata
from blizzard.runner.store import MIGRATIONS_DIR as RUNNER_MIGRATIONS_DIR
from blizzard.runner.store.schema import metadata as runner_metadata
from blizzard.tools.store_contract import document_from_metadata

# Reading a tree's head needs a URL but never connects to it.
_NO_STORE_URL = "sqlite:////dev/null"

STORES: tuple[tuple[str, MetaData, Path], ...] = (
    ("hub", hub_metadata, HUB_MIGRATIONS_DIR),
    ("runner", runner_metadata, RUNNER_MIGRATIONS_DIR),
)


def _dump(document: dict) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def export(out_dir: Path) -> list[Path]:
    """Write each store's schema document under ``out_dir/store``; return the written paths."""
    store_dir = out_dir / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, metadata, migrations_dir in STORES:
        head = MigrationRunner(script_location=migrations_dir, url=_NO_STORE_URL).script_head()
        path = store_dir / f"{name}.json"
        path.write_text(_dump(document_from_metadata(name, metadata, head)))
        written.append(path)
    return written


@click.command()
@click.option(
    "--out-dir",
    default="contracts",
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to write the contract documents into.",
)
def main(out_dir: Path) -> None:
    """Dump the store schema documents for drift checks and the reflection test."""
    for path in export(out_dir):
        click.echo(f"wrote {path}")


if __name__ == "__main__":
    main()
