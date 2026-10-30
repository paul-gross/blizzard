"""The old address fields through a real hub process and the ``blizzard hub`` CLI: a configuration document written
before forges were records applies unchanged as version 1, a warning and a ``config-old-field-used`` event per old
field; an entry that leaves a work source's old fields out keeps its forge; and ``source edit --clear api-base`` moves
the source to the ``github`` forge with a warning (service tier). Run with ``BLIZZARD_SERVICE=1``."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx2
import pytest
import yaml

from tests.fleet_launchers import run_hub
from tests.mock_fleet_support import service_gate
from tests.support import FIXTURE_FORGE_SECRET, free_port

pytestmark = [pytest.mark.service, service_gate]

_BLIZZARD_BIN = Path(sys.executable).parent / "blizzard"
_GHE = "https://ghe.acme.internal/api/v3"

#: A version-1 document as an operator kept it before forges: every record named by its addresses.
_DOCUMENT = {
    "version": 1,
    "work_sources": [
        {
            "slug": "enterprise-issues",
            "provider": "github",
            "locator": "acme/issues",
            "api_base": _GHE,
            "web_base": "https://ghe.acme.internal",
            "secret": FIXTURE_FORGE_SECRET,
        }
    ],
    "repositories": [
        {
            "slug": "enterprise-widget",
            "forge_api_url": _GHE,
            "owner": "acme",
            "repo": "widget",
            "base_branch": "main",
            "secret_name": FIXTURE_FORGE_SECRET,
        }
    ],
}


def _blizzard(hub_port: int, *args: str) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(
        [str(_BLIZZARD_BIN), *args, "--hub-url", f"http://127.0.0.1:{hub_port}"], capture_output=True, text=True
    )
    assert done.returncode == 0, f"blizzard {' '.join(args)} failed:\n{done.stdout}\n{done.stderr}"
    return done


def _old_field_events(hub: httpx2.Client) -> list[dict[str, Any]]:
    events = hub.get("/api/events", params={"severity": "warning"}).json()["events"]
    return [event["detail"] for event in reversed(events) if event["kind"] == "config-old-field-used"]


def _document(path: Path, document: dict[str, Any]) -> str:
    path.write_text(yaml.safe_dump(document, sort_keys=False))
    return str(path)


def test_the_old_fields_resolve_to_forges_with_a_warning_at_every_door(tmp_path: Path) -> None:
    hub_port = free_port()
    with run_hub(tmp_path / "hub", free_port(), hub_port) as hub:
        seeded = len(_old_field_events(hub))

        applied = _blizzard(hub_port, "hub", "config", "apply", _document(tmp_path / "before.yaml", _DOCUMENT))
        warnings = [line for line in applied.stderr.splitlines() if line.startswith("warning: ")]
        assert len(warnings) == 3, applied.stderr
        events = _old_field_events(hub)[seeded:]
        assert [e["field"] for e in events] == ["api_base", "web_base", "forge_api_url"]
        assert {e["forge_slug"] for e in events} == {"ghe-acme-internal"}
        source = hub.get("/api/work-sources/enterprise-issues").json()
        repository = hub.get("/api/repositories/enterprise-widget").json()
        assert source["forge_id"] == repository["forge_id"]
        assert (source["forge_slug"], source["api_base"], source["web_base"]) == (
            "ghe-acme-internal",
            _GHE,
            "https://ghe.acme.internal",
        )

        silent = {
            "version": 1,
            "work_sources": [{k: v for k, v in _DOCUMENT["work_sources"][0].items() if not k.endswith("_base")}],
        }
        silent["work_sources"][0]["annotate"] = True
        kept = _blizzard(hub_port, "hub", "config", "apply", _document(tmp_path / "silent.yaml", silent))
        assert "warning: " not in kept.stderr, kept.stderr
        assert hub.get("/api/work-sources/enterprise-issues").json()["forge_slug"] == "ghe-acme-internal"

        cleared = _blizzard(hub_port, "hub", "source", "edit", "enterprise-issues", "--clear", "api-base")
        assert any("api_base" in line and "github" in line for line in cleared.stderr.splitlines()), cleared.stderr
        moved = hub.get("/api/work-sources/enterprise-issues").json()
        assert (moved["forge_slug"], moved["api_base"]) == ("github", "https://api.github.com")
