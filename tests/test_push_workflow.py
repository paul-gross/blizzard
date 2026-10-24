"""``.github/workflows/push.yml``'s dev build and dogfood image channel.
YAML assertions plus shell execution of the version step; no Docker/GHCR
credentials or live image build.
"""

from __future__ import annotations

import os
import re
import subprocess

import pytest
import yaml

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_REPO_ROOT = repo_root()
_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "push.yml"


def _jobs() -> dict:
    workflow = yaml.safe_load(_WORKFLOW_PATH.read_text())
    return workflow["jobs"]


def _dev_image_job() -> dict:
    return _jobs()["dev-image"]


def _step_index(steps: list[dict], predicate) -> int:
    for i, step in enumerate(steps):
        if predicate(step):
            return i
    raise AssertionError(f"no step matched among: {[s.get('name') or s.get('uses') for s in steps]}")


def test_push_workflow_exists() -> None:
    assert _WORKFLOW_PATH.is_file()


def test_dev_image_job_waits_for_gate_and_upper_tiers() -> None:
    """The dev channel publishes only a *proven* master commit — it must not
    race ahead of either the merge gate or the push workflow's upper
    verification tiers (service tier + bounded crash sweep)."""
    needs = _dev_image_job()["needs"]
    assert "gate" in needs
    assert "upper-tiers" in needs


def test_dev_image_job_needs_dev_build_for_the_shared_version_string() -> None:
    """dev-image consumes dev-build's computed next-minor dev version
    for its OCI version annotation rather than recomputing it inline — one
    source of truth for what "the dev version" means for a given commit."""
    assert "dev-build" in _dev_image_job()["needs"]


def test_dev_image_needs_exactly_the_gate_the_upper_tiers_the_dev_build_and_the_rehearsal() -> None:
    assert _dev_image_job()["needs"] == ["gate", "upper-tiers", "dev-build", "rehearsal"]
    assert not [name for name in _jobs() if "compat" in name]


def test_the_rehearsal_upgrades_to_the_wheel_dev_build_uploaded() -> None:
    job = _jobs()["rehearsal"]
    assert job["uses"] == "./.github/workflows/rehearsal.yml"
    assert job["needs"] == ["gate", "dev-build"]
    upload = next(s for s in _jobs()["dev-build"]["steps"] if "upload-artifact" in str(s.get("uses", "")))
    assert job["with"] == {"candidate_artifact": "blizzard-dev-wheel-${{ needs.dev-build.outputs.version }}"}
    assert upload["with"]["name"] == "blizzard-dev-wheel-${{ steps.ver.outputs.version }}"


def test_dev_build_job_exposes_its_version_as_an_output() -> None:
    dev_build = _jobs()["dev-build"]
    assert dev_build.get("outputs", {}).get("version")


def test_the_dev_version_carries_the_commit_it_was_built_from() -> None:
    """Pinned because it is one interpolation in a shell line, easy to drop while
    tidying, and nothing else fails when it goes."""
    steps = _jobs()["dev-build"]["steps"]
    compute = next(s for s in steps if s.get("id") == "ver")
    assert "GITHUB_SHA" in compute["run"], (
        "the dev version must embed the commit sha as a PEP 440 local segment "
        "(X.(Y+1).0.dev<run>+<sha>) — see the step's own comment for why"
    )


@pytest.mark.parametrize("cut_version, expected", [("0.1.0", "0.2.0"), ("0.2.0", "0.3.0")])
def test_dev_version_targets_the_next_minor_train(tmp_path, cut_version: str, expected: str) -> None:
    assert _dev_version(tmp_path, cut_version, 'floor = "0.2"\n') == f"{expected}.dev42+abcdef123"


def test_an_open_major_builds_toward_its_first_release(tmp_path) -> None:
    assert _dev_version(tmp_path, "0.2.0", 'floor = "0.2"\nopening = "1"\n') == "1.0.0.dev42+abcdef123"


def test_a_missing_window_file_fails_the_version_step(tmp_path) -> None:
    compute = next(s for s in _jobs()["dev-build"]["steps"] if s.get("id") == "ver")
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.2.0"\n')
    script = compute["run"].replace("${{ github.run_number }}", "42")
    result = subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=tmp_path,
        env={**os.environ, "GITHUB_SHA": "abcdef123456789", "GITHUB_OUTPUT": str(tmp_path / "output")},
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0


def _dev_version(tmp_path, cut_version: str, window: str) -> str:
    compute = next(s for s in _jobs()["dev-build"]["steps"] if s.get("id") == "ver")
    (tmp_path / "pyproject.toml").write_text(f'[project]\nversion = "{cut_version}"\n')
    (tmp_path / "src" / "blizzard" / "wire").mkdir(parents=True)
    (tmp_path / "src" / "blizzard" / "wire" / "window.toml").write_text(window)
    output = tmp_path / "output"
    script = compute["run"].replace("${{ github.run_number }}", "42")
    result = subprocess.run(
        ["bash", "-e", "-c", script],
        cwd=tmp_path,
        env={**os.environ, "GITHUB_SHA": "abcdef123456789", "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return output.read_text().strip().removeprefix("version=")


def test_dev_image_puts_a_wheel_in_dist_before_building() -> None:
    """A bare checkout has no `dist/`, so the job must stage the wheel there before
    the build-push step."""
    steps = _dev_image_job()["steps"]
    fetch = _step_index(steps, lambda s: "download-artifact" in str(s.get("uses", "")))
    build = _step_index(steps, lambda s: "docker buildx build" in str(s.get("run", "")))
    assert fetch < build, "the wheel must be staged before the image build consumes it"
    assert steps[fetch].get("with", {}).get("path") == "dist"


def test_dev_image_job_declares_packages_write() -> None:
    permissions = _dev_image_job().get("permissions")
    assert permissions, "dev-image must declare its own job-level permissions block to push to GHCR"
    assert permissions.get("packages") == "write"


def test_derived_tags_include_edge_and_a_sha_tag_referencing_github_sha() -> None:
    steps = _dev_image_job()["steps"]
    derive = steps[_step_index(steps, lambda s: s.get("id") == "image_meta")]
    run = str(derive.get("run", ""))
    assert "ghcr.io/paul-gross/blizzard-hub:edge" in run
    assert "sha-${GITHUB_SHA}" in run or "sha-$GITHUB_SHA" in run


def _build_push_step() -> dict:
    steps = _dev_image_job()["steps"]
    return steps[_step_index(steps, lambda s: "docker buildx build" in str(s.get("run", "")))]


def test_build_push_step_consumes_the_derived_tags_and_targets_both_platforms() -> None:
    build_push = _build_push_step()
    env = build_push.get("env", {})
    assert "image_meta" in str(env.get("IMAGE_TAGS", "")), (
        "the build-push step must consume the derived tag list, not a literal"
    )
    assert "image_meta" in str(env.get("IMAGE_ANNOTATIONS", ""))
    run = str(build_push.get("run", ""))
    assert "--push" in run
    assert "linux/amd64" in run
    assert "linux/arm64" in run


def test_build_push_step_retries_a_transient_ghcr_error_before_failing() -> None:
    run = str(_build_push_step().get("run", ""))
    assert "is-retryable-push-error.sh" in run
    assert "sleep" in run
    max_attempts = next(
        int(line.split("=", 1)[1]) for line in run.splitlines() if line.strip().startswith("max_attempts=")
    )
    assert max_attempts >= 3, "at least two retries (three total attempts) are required"


def test_only_dev_image_holds_the_signing_permissions() -> None:
    """Keyless signing and attestation need `id-token: write` and
    `attestations: write`; granting either anywhere else widens who can mint
    the master identity."""
    workflow = yaml.safe_load(_WORKFLOW_PATH.read_text())
    assert "id-token" not in workflow.get("permissions", {})
    assert "attestations" not in workflow.get("permissions", {})
    for name, job in _jobs().items():
        permissions = job.get("permissions", {})
        held = {"id-token", "attestations"} & {k for k, v in permissions.items() if v == "write"}
        if name == "dev-image":
            assert held == {"id-token", "attestations"}
        else:
            assert not held, f"{name} must not hold {held}"


def test_dev_image_signs_and_attests_the_pushed_digest_after_the_push() -> None:
    steps = _dev_image_job()["steps"]
    push_idx = _step_index(steps, lambda s: s.get("id") == "build_push")
    sign_idx = _step_index(steps, lambda s: "cosign sign --yes --recursive" in str(s.get("run", "")))
    attest_idx = _step_index(steps, lambda s: "attest-build-provenance" in str(s.get("uses", "")))
    assert push_idx < sign_idx
    assert push_idx < attest_idx
    assert "--sbom" not in str(steps[sign_idx]["run"])
    assert steps[attest_idx]["with"]["push-to-registry"] is True
    assert steps[attest_idx]["with"]["subject-digest"] == "${{ steps.build_push.outputs.digest }}"


def test_dev_image_signing_actions_are_pinned_by_sha() -> None:
    steps = _dev_image_job()["steps"]
    for fragment in ("cosign-installer", "attest-build-provenance"):
        uses = steps[_step_index(steps, lambda s, f=fragment: f in str(s.get("uses", "")))]["uses"]
        assert re.fullmatch(r".+@[0-9a-f]{40}", uses), uses


def test_push_triggers_on_master_alone() -> None:
    workflow = yaml.safe_load(_WORKFLOW_PATH.read_text())
    assert workflow[True] == {"push": {"branches": ["master"]}}


def test_proven_against_records_both_shas_after_the_gate_and_upper_tiers() -> None:
    job = _jobs()["proven-against"]
    assert set(job["needs"]) == {"gate", "upper-tiers"}
    record = next(s for s in job["steps"] if "run" in s)
    assert record["env"]["BLIZZARD_SHA"] == "${{ github.sha }}"
    assert record["env"]["MOCK_SHA"] == "${{ needs.upper-tiers.outputs.mock_sha }}"
    assert '"blizzard-mock"' in record["run"] and "blizzard:" in record["run"]


def test_proven_against_uploads_the_record_under_its_name() -> None:
    upload = next(
        s for s in _jobs()["proven-against"]["steps"] if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert upload["with"]["name"] == "proven-against"
    assert upload["with"]["if-no-files-found"] == "error"


def test_push_runs_the_service_tier_and_the_bounded_sweep_only() -> None:
    job = _jobs()["upper-tiers"]
    assert job["uses"] == "./.github/workflows/upper-tiers.yml"
    assert job["with"] == {"crash_sweep_ci": "1"}
