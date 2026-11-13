"""``.github/workflows/postgres-uow-concurrency.yml`` — the nightly proof: scheduled or dispatched only, read-only, the
rehearsal's Postgres service, and the proof script run against it."""

from __future__ import annotations

import yaml

from tests.support.repo_files import repo_root

_WORKFLOW = yaml.safe_load((repo_root() / ".github" / "workflows" / "postgres-uow-concurrency.yml").read_text())
_JOB = _WORKFLOW["jobs"]["postgres-uow-concurrency"]


def test_it_runs_on_a_schedule_and_on_demand_only() -> None:
    # YAML 1.1 reads the bare key ``on`` as ``True``.
    triggers = _WORKFLOW[True]
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    assert len(triggers["schedule"]) == 1
    assert triggers["schedule"][0]["cron"]


def test_it_reads_only() -> None:
    assert _WORKFLOW["permissions"] == {"contents": "read"}


def test_it_runs_the_proof_against_the_postgres_service_the_rehearsal_uses() -> None:
    rehearsal = yaml.safe_load((repo_root() / ".github" / "workflows" / "rehearsal.yml").read_text())
    assert _JOB["services"] == rehearsal["jobs"]["leg"]["services"]
    run = next(step for step in _JOB["steps"] if "check_hub_uow_postgres" in str(step.get("run", "")))
    assert run["run"] == "uv run --extra postgres python scripts/check_hub_uow_postgres.py"
    assert run["env"]["BLIZZARD_UOW_POSTGRES_URL"] == "postgresql://postgres:postgres@localhost:5432/postgres"
