"""``.github/workflows/upper-tiers.yml``'s job roster and the stable names the required checks match."""

from __future__ import annotations

import pytest
import yaml

from tests.support.repo_files import repo_root

_WORKFLOW = yaml.safe_load((repo_root() / ".github" / "workflows" / "upper-tiers.yml").read_text())
_JOBS = _WORKFLOW["jobs"]
_SUITE_JOBS = ("ui", "journey", "image-smoke", "compose-smoke")


def test_the_full_suite_input_defaults_off_and_the_crash_sweep_defaults_bounded() -> None:
    inputs = _WORKFLOW[True]["workflow_call"]["inputs"]
    assert inputs["full_suite"]["default"] is False
    assert inputs["crash_sweep_ci"]["default"] == "1"


@pytest.mark.parametrize("job", _SUITE_JOBS)
def test_a_suite_job_runs_only_when_the_full_suite_is_asked_for(job: str) -> None:
    assert _JOBS[job]["if"] == "${{ inputs.full_suite }}"


@pytest.mark.parametrize("job", ("service-tier-result", "crash-sweep", *_SUITE_JOBS))
def test_a_tier_job_name_carries_its_method_id_and_never_its_profile(job: str) -> None:
    name = _JOBS[job]["name"]
    assert name.endswith(")") and "(blizzard:" in name
    assert "profile" not in name.lower()


def test_the_check_names_the_branch_protection_requires_are_the_ones_the_jobs_emit() -> None:
    assert {_JOBS[job]["name"] for job in ("service-tier-result", "crash-sweep", *_SUITE_JOBS)} == {
        "service tier (blizzard:service-test)",
        "kill-9 crash sweep (blizzard:crash-sweep)",
        "ui (blizzard:ui)",
        "acceptance journey (blizzard:journey)",
        "hub image smoke (blizzard:image-smoke)",
        "reference compose smoke (blizzard:compose-smoke)",
    }


def test_playwright_uv_bootstrap_has_a_pinned_alternate_source_without_weakening_tests() -> None:
    steps = _JOBS["service-playwright"]["steps"]
    primary = next(step for step in steps if step.get("id") == "setup-uv")
    fallback = next(step for step in steps if step.get("name") == "Install uv from PyPI if GitHub setup failed")
    python = next(step for step in steps if step.get("name") == "Python for uv bootstrap")
    mise = next(step for step in steps if step.get("name") == "Node from mise.toml")
    execution = next(
        step for step in steps if step.get("name") == "service tier — the Playwright half, `mise run playwright`"
    )
    assert (
        steps.index(python) < steps.index(primary) < steps.index(fallback) < steps.index(mise) < steps.index(execution)
    )
    assert python["with"]["python-version"] == "3.12"
    assert primary["with"]["version"] == "0.13.6"
    assert "../blizzard-mock/uv.lock" in primary["with"]["cache-dependency-glob"]
    assert primary["continue-on-error"] is True
    assert fallback["if"] == "steps.setup-uv.outcome == 'failure'"
    assert fallback["run"] == "python -m pip install 'uv==0.13.6'"
    assert not fallback.get("continue-on-error", False)
    assert mise["with"]["install_args"] == "node"
    assert not execution.get("continue-on-error", False)
    assert "--shard=${{ matrix.shard }}/${{ matrix.shards }}" in execution["run"]
