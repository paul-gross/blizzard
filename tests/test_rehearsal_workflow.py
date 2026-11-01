"""``.github/workflows/rehearsal.yml`` — its trigger, its never-empty matrix, and its log upload."""

from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

import pytest
import yaml

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_WORKFLOW_PATH = repo_root() / ".github" / "workflows" / "rehearsal.yml"


def _workflow() -> dict[Any, Any]:
    return yaml.safe_load(_WORKFLOW_PATH.read_text())


def _step(job: str, predicate) -> dict[str, Any]:
    return next(s for s in _workflow()["jobs"][job]["steps"] if predicate(s))


def test_the_rehearsal_is_only_ever_called() -> None:
    trigger = _workflow()[True]
    assert list(trigger) == ["workflow_call"]
    assert set(trigger["workflow_call"]["inputs"]) == {"candidate_artifact", "tag"}
    assert _workflow()["permissions"] == {"contents": "read"}


def _matrix_filter() -> str:
    run = _step("releases", lambda s: s.get("id") == "list")["run"]
    found = re.search(r"jq -c --argjson published \"\$published\" '([^']+)'", run)
    assert found is not None
    return found.group(1)


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
@pytest.mark.parametrize(
    ("listed", "published", "expected"),
    [
        (
            '{"releases": [{"tag": "v0.2.0-rc.2"}, {"tag": "v0.1.0"}]}',
            '["v0.1.0","v0.2.0-rc.2"]',
            '["v0.2.0-rc.2","v0.1.0"]',
        ),
        ('{"releases": [{"tag": "v0.1.0"}, {"tag": "v0.2.0-rc.1"}]}', '["v0.1.0"]', '["v0.1.0"]'),
        ('{"releases": [{"tag": "v0.2.0-rc.1"}]}', "[]", '[""]'),
        ('{"releases": []}', "[]", '[""]'),
    ],
)
def test_the_matrix_holds_the_published_releases_and_is_never_empty(listed: str, published: str, expected: str) -> None:
    done = subprocess.run(
        ["jq", "-c", "--argjson", "published", published, _matrix_filter()],
        input=listed, capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert done.stdout.strip() == expected


def test_the_published_releases_are_read_from_the_repository() -> None:
    step = _step("releases", lambda s: s.get("id") == "list")
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert 'gh release list --repo "$GITHUB_REPOSITORY" --exclude-drafts' in step["run"]


def test_a_tag_run_leaves_the_tag_under_test_out() -> None:
    step = _step("releases", lambda s: s.get("id") == "list")
    assert step["env"]["TAG"] == "${{ inputs.tag }}"
    assert '--tag "${TAG#v}"' in step["run"]


def test_each_leg_is_one_release_on_one_backend() -> None:
    leg = _workflow()["jobs"]["leg"]
    assert leg["strategy"]["matrix"] == {
        "baseline": "${{ fromJSON(needs.releases.outputs.baselines) }}",
        "backend": ["sqlite", "postgres"],
    }
    assert leg["services"]["postgres"]["image"] == "postgres:16"
    mock = _step("leg", lambda s: s.get("uses") == "./blizzard/.github/actions/resolve-mock-ref")
    assert mock["with"] == {"baseline": "${{ matrix.baseline }}"}


def test_a_leg_with_no_baseline_says_so_and_passes() -> None:
    run = _step("leg", lambda s: "pytest -m rehearsal" in str(s.get("run", "")))["run"]
    assert "no release is supported" in run.split("pytest")[0]


def test_a_failed_leg_uploads_its_logs_under_its_baseline_and_backend() -> None:
    upload = _step("leg", lambda s: str(s.get("uses", "")).startswith("actions/upload-artifact@"))
    assert upload["if"] == "failure()"
    assert re.fullmatch(r"actions/upload-artifact@[0-9a-f]{40}", upload["uses"])
    assert "matrix.baseline" in upload["with"]["name"] and "matrix.backend" in upload["with"]["name"]
    assert upload["with"]["path"].startswith("${{ env.BLIZZARD_REHEARSAL_ROOT }}/")
