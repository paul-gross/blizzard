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
    assert job["if"] == "${{ github.ref_type != 'tag' && !inputs.lint_only }}"
    assert job["steps"][0]["with"]["fetch-depth"] == 0


def test_the_event_commits_reach_the_command_through_env_not_interpolation() -> None:
    step = next(s for s in _jobs()["compat"]["steps"] if "blizzard-compat" in str(s.get("run", "")))
    assert set(step["env"]) == {"RANGE_BASE", "RANGE_HEAD", "LINE_REF"}
    assert "${{" not in step["run"]
    assert "github.event.pull_request.base.sha" in step["env"]["RANGE_BASE"]
    assert "github.event.before" in step["env"]["RANGE_BASE"]


def test_the_per_commit_wire_check_is_retired() -> None:
    assert [name for name in _jobs() if "compat" in name] == ["compat"]


def test_refusal_census_runs_once_beside_the_shards_with_no_dependency_on_them() -> None:
    job = _jobs()["refusal-census"]
    assert "needs" not in job
    assert job["if"] == "${{ !inputs.lint_only }}"
    command = "uv run python -m tests.support.aggregate_census"
    steps = [step for step in job["steps"] if step.get("run") == command]
    assert len(steps) == 1
    assert "if" not in steps[0] and "continue-on-error" not in steps[0]
    assert "BLIZZARD_TEST_SHARD" not in steps[0].get("env", {})
    checkouts = [step for step in job["steps"] if str(step.get("uses", "")).startswith("actions/checkout")]
    assert [step["with"]["persist-credentials"] for step in checkouts] == [False]
    assert "aggregate_census" not in str(_jobs()["python-tests-result"])
    assert command in (repo_root() / "scripts/ci-gate.sh").read_text()


def test_the_required_pytest_check_joins_every_shard_and_the_census_on_success_only() -> None:
    job = _jobs()["python-tests-result"]
    assert job["name"] == "pytest (unit + component)"
    assert job["needs"] == ["python-tests", "refusal-census"]
    assert job["if"] == "${{ !cancelled() && !inputs.lint_only }}"
    (step,) = job["steps"]
    assert set(step["env"].values()) == {"${{ needs.python-tests.result }}", "${{ needs.refusal-census.result }}"}
    for variable in step["env"]:
        assert f'test "${variable}" = success' in step["run"]


def test_each_pytest_shard_records_its_timings_and_keeps_only_that_file_on_success() -> None:
    steps = _jobs()["python-tests"]["steps"]
    timings = "timings-gate-pytest-${{ matrix.shard }}"
    assert next(s for s in steps if s.get("name") == "pytest")["env"]["BLIZZARD_TEST_TIMINGS"] == f"{timings}.json"
    [upload] = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert upload["if"] == "${{ success() }}"
    assert upload["with"]["name"] == timings
    assert upload["with"]["path"] == f"{timings}.json"
    assert upload["with"]["retention-days"] <= 14
