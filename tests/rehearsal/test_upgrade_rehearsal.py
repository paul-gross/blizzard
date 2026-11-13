"""Rehearse a seeded release's hub and runner upgrade, one baseline and backend per leg.

Upgrade the hub while its old runner works, then upgrade the runner. Walk both stores back
to the baseline and forward again. Run with ``mise run rehearsal`` and ``BLIZZARD_REHEARSAL_*`` inputs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import ExitStack
from pathlib import Path

import pytest

from blizzard.tools.compat.releases import parse_releases
from blizzard.tools.release_install import Installation, download_release, install
from tests.rehearsal.comparison import (
    reflection_drift,
)
from tests.rehearsal.installation import Hub, Leg
from tests.rehearsal.journey import Rehearsal
from tests.rehearsal.stores import (
    postgres_database,
    reflect,
)
from tests.support.fleet_launchers import mock_bin_dir
from tests.support.repo_files import repo_root

_GATE = "BLIZZARD_REHEARSAL"
_BASELINE = "BLIZZARD_REHEARSAL_BASELINE"
_CANDIDATE = "BLIZZARD_REHEARSAL_CANDIDATE"
_PG_URL = "BLIZZARD_REHEARSAL_PG_URL"
_ROOT = "BLIZZARD_REHEARSAL_ROOT"

pytestmark = [
    pytest.mark.rehearsal,
    pytest.mark.skipif(
        os.environ.get(_GATE) != "1", reason=f"the upgrade rehearsal runs real installations; set {_GATE}=1"
    ),
]


def _baselines() -> list[str | None]:
    """The tags to rehearse from: the one the caller names, else every published supported release; ``None`` when none is."""
    named = os.environ.get(_BASELINE)
    if named:
        return [named]
    if os.environ.get(_GATE) != "1":
        return [None]
    listed = subprocess.run(
        [str(Path(sys.executable).parent / "blizzard-compat"), "releases", "--json"],
        capture_output=True, text=True, check=True, cwd=repo_root(),
    )  # fmt: skip
    published = subprocess.run(
        ["gh", "release", "list", "--exclude-drafts", "--limit", "1000", "--json", "tagName", "--jq", ".[].tagName"],
        capture_output=True, text=True, check=True, cwd=repo_root(),
    ).stdout.split()  # fmt: skip
    tags = [release["tag"] for release in json.loads(listed.stdout)["releases"]]
    return [release.tag for release in parse_releases(tag for tag in tags if tag in published)] or [None]


@pytest.fixture(scope="module")
def work_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    named = os.environ.get(_ROOT)
    root = Path(named) if named else tmp_path_factory.mktemp("rehearsal")
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture(scope="module")
def candidate(work_root: Path) -> Installation:
    source = os.environ.get(_CANDIDATE)
    if not source:
        pytest.skip(f"{_CANDIDATE} names no candidate directory (mise run rehearsal builds one)")
    return install("candidate", Path(source), work_root / "venvs" / "candidate")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("baseline_tag", _baselines())
def test_a_supported_release_upgrades_to_the_candidate_and_walks_back(
    baseline_tag: str | None, backend: str, candidate: Installation, work_root: Path
) -> None:
    """Seed the baseline, upgrade the hub, keep the old runner, upgrade the runner, then walk both stores."""
    if baseline_tag is None:
        pytest.skip("no release is supported, so there is no upgrade to rehearse")
    server_url = os.environ.get(_PG_URL)
    if backend == "postgres" and not server_url:
        pytest.skip(f"{_PG_URL} is unset, so the Postgres backend is not rehearsed")
    named_mock = os.environ.get("BLIZZARD_REHEARSAL_MOCK_BIN")
    mock_bin = Path(named_mock) if named_mock else mock_bin_dir()
    if mock_bin is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    root = work_root / f"{baseline_tag}-{backend}"
    baseline = install(
        baseline_tag, download_release(baseline_tag, work_root / "releases" / baseline_tag), root / "venv"
    )
    with ExitStack() as stack:
        db_url = stack.enter_context(postgres_database(server_url)) if server_url and backend == "postgres" else None
        Rehearsal(Leg(baseline_tag, backend), baseline, candidate, mock_bin, root, db_url, stack).run()
        if server_url and backend == "postgres":
            _reflect_an_empty_postgres_store(Leg(baseline_tag, backend), candidate, root, server_url, stack)


def _reflect_an_empty_postgres_store(
    leg: Leg, candidate: Installation, root: Path, server_url: str, stack: ExitStack
) -> None:
    """The candidate's migrations on an empty Postgres store reflect to the committed hub document."""
    url = stack.enter_context(postgres_database(server_url))
    Hub(root / "empty-hub", url).init(candidate)
    committed = json.loads((repo_root() / "contracts" / "store" / "hub.json").read_text())
    drift = reflection_drift(reflect(url, "hub"), committed)
    if drift:
        raise leg.fail("postgres reflection", "the migrated store is not the committed document:\n" + "\n".join(drift))
