"""``.github/workflows/gate.yml``'s compatibility job: when it runs and how it reads the event."""

from __future__ import annotations

from typing import Any

import yaml

from tests.support.repo_files import repo_root

_WORKFLOW_PATH = repo_root() / ".github" / "workflows" / "gate.yml"


def _jobs() -> dict[str, Any]:
    return yaml.safe_load(_WORKFLOW_PATH.read_text())["jobs"]


def test_the_compat_job_checks_out_all_history_on_every_trigger_but_a_tag() -> None:
    job = _jobs()["compat"]
    assert job["name"] == "compatibility against supported releases"
    assert job["if"] == "github.ref_type != 'tag'"
    assert job["steps"][0]["with"]["fetch-depth"] == 0


def test_the_event_commits_reach_the_command_through_env_not_interpolation() -> None:
    step = next(s for s in _jobs()["compat"]["steps"] if "blizzard-compat" in str(s.get("run", "")))
    assert set(step["env"]) == {"RANGE_BASE", "RANGE_HEAD", "LINE_REF"}
    assert "${{" not in step["run"]
    assert "github.event.pull_request.base.sha" in step["env"]["RANGE_BASE"]
    assert "github.event.before" in step["env"]["RANGE_BASE"]


def test_the_per_commit_wire_check_is_retired() -> None:
    assert [name for name in _jobs() if "compat" in name] == ["compat"]


def test_refusal_census_runs_unsharded_in_the_required_pytest_result_and_local_gate() -> None:
    job = _jobs()["python-tests-result"]
    assert job["needs"] == ["python-tests"]
    command = "uv run python -m tests.support.aggregate_census"
    steps = [step for step in job["steps"] if step.get("run") == command]
    assert len(steps) == 1
    assert "if" not in steps[0] and "continue-on-error" not in steps[0]
    assert "BLIZZARD_TEST_SHARD" not in steps[0].get("env", {})
    assert command in (repo_root() / "scripts/ci-gate.sh").read_text()
