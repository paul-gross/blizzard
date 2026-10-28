"""Export the committed contract documents to a stable path.

That is each store's schema document (``contracts/store/<store>.json``), built from its declared store schema and its
migration tree's head revision alone — no store is opened — and each daemon's config document
(``contracts/config/<daemon>.json``), built from its config registry. Both are dumped as deterministic, sorted JSON, so
a drift check over the committed output is stable."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
from sqlalchemy import MetaData

from blizzard.foundation.config_keys import ConfigRegistry
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.config_keys import HUB_REGISTRY
from blizzard.hub.store import MIGRATIONS_DIR as HUB_MIGRATIONS_DIR
from blizzard.hub.store.schema import metadata as hub_metadata
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.store import MIGRATIONS_DIR as RUNNER_MIGRATIONS_DIR
from blizzard.runner.store.schema import metadata as runner_metadata
from blizzard.tools.config_contract import document_from_registry
from blizzard.tools.store_contract import document_from_metadata

# Reading a tree's head needs a URL but never connects to it.
_NO_STORE_URL = "sqlite:////dev/null"

STORES: tuple[tuple[str, MetaData, Path], ...] = (
    ("hub", hub_metadata, HUB_MIGRATIONS_DIR),
    ("runner", runner_metadata, RUNNER_MIGRATIONS_DIR),
)

#: Each daemon's config document is named for its toml file's owner: the hub and the host.
CONFIGS: tuple[tuple[str, ConfigRegistry], ...] = (("hub", HUB_REGISTRY), ("host", HOST_REGISTRY))


def _dump(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def export(out_dir: Path) -> list[Path]:
    """Write each store's schema document under ``out_dir/store`` and each daemon's config document under
    ``out_dir/config``; return the written paths."""
    store_dir = out_dir / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, metadata, migrations_dir in STORES:
        head = MigrationRunner(script_location=migrations_dir, url=_NO_STORE_URL).script_head()
        path = store_dir / f"{name}.json"
        path.write_text(_dump(document_from_metadata(name, metadata, head)))
        written.append(path)
    config_dir = out_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    for name, registry in CONFIGS:
        path = config_dir / f"{name}.json"
        path.write_text(_dump(document_from_registry(registry)))
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
    """Dump the store schema and daemon config documents for drift checks and the reflection tests."""
    for path in export(out_dir):
        click.echo(f"wrote {path}")


if __name__ == "__main__":
    main()
