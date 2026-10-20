"""``.github/workflows/store-baseline.yml`` — the reusable proof job: its Postgres service, token, and command."""

from __future__ import annotations

import pytest
import yaml

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_WORKFLOW = yaml.safe_load((repo_root() / ".github" / "workflows" / "store-baseline.yml").read_text())
_JOB = _WORKFLOW["jobs"]["store-baseline"]


def test_it_is_called_never_triggered_and_reads_only() -> None:
    assert _WORKFLOW[True] == {"workflow_call": None}
    assert _WORKFLOW["permissions"] == {"contents": "read"}


def test_it_runs_the_proof_against_the_postgres_service_the_rehearsal_uses() -> None:
    rehearsal = yaml.safe_load((repo_root() / ".github" / "workflows" / "rehearsal.yml").read_text())
    assert _JOB["services"] == rehearsal["jobs"]["leg"]["services"]
    run = next(step for step in _JOB["steps"] if "pytest" in str(step.get("run", "")))
    assert run["run"] == "uv run --extra postgres pytest -m store_baseline tests/test_store_baseline.py -v"
    assert run["env"]["BLIZZARD_STORE_BASELINE"] == "1"
    assert run["env"]["BLIZZARD_STORE_BASELINE_PG_URL"] == "postgresql://postgres:postgres@localhost:5432/postgres"
    assert run["env"]["GH_TOKEN"] == "${{ github.token }}"


def test_it_fetches_every_tag_so_the_floors_store_contract_can_be_read() -> None:
    checkout = next(s for s in _JOB["steps"] if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0
