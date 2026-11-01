"""``.github/workflows/gate.yml``'s compatibility job: when it runs and how it reads the event."""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

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
