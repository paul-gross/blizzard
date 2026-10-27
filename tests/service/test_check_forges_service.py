"""The forge pre-flight check as an operator runs it, from the build about to be installed against a store from before
the forge migration: ``blizzard hub config check-forges`` names a refusable record, and ``blizzard hub migrate`` names
the same record when it refuses to carry the store over, leaving it where it was (service tier). A hub of this build
refuses to start on that store at all, so the hub that serves it while the check runs is the previous build's. Run with
``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.config_inputs import init_hub, migrate_hub
from tests.forge_check_support import seed_addresses
from tests.service.support import service_gate

pytestmark = [pytest.mark.service, service_gate]

_BLIZZARD_BIN = Path(sys.executable).parent / "blizzard"
_REFUSED_SLUG = "userful"
#: The chain's head before the forge migration — the store a hub before it serves.
_BEFORE_FORGES = "20261024_0900_hub_legacy_run"


def _blizzard(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(_BLIZZARD_BIN), *args], capture_output=True, text=True)


def test_the_command_and_the_migration_name_the_same_refusable_record(tmp_path: Path) -> None:
    hub_dir = tmp_path / "hub"
    config = init_hub(hub_dir)
    migrate_hub(hub_dir, down=_BEFORE_FORGES)
    seed_addresses(
        config.db_url,
        repository_rows=((_REFUSED_SLUG, "https://user@forge.example", "acme", "widget"),),
        sealed_secret=False,
    )

    checked = _blizzard("hub", "config", "check-forges", "--dir", str(hub_dir))
    assert checked.returncode == 1, checked.stdout + checked.stderr
    assert _REFUSED_SLUG in checked.stdout and "needs-cleaning" in checked.stdout, checked.stdout

    migrated = _blizzard("hub", "migrate", "--dir", str(hub_dir))
    assert migrated.returncode != 0, migrated.stdout + migrated.stderr
    assert _REFUSED_SLUG in migrated.stdout + migrated.stderr

    again = _blizzard("hub", "config", "check-forges", "--dir", str(hub_dir))
    assert (again.returncode, again.stdout) == (checked.returncode, checked.stdout)
