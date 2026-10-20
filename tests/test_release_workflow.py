"""``.github/workflows/release.yml`` — release ordering, permissions, and
version stamping. Parse YAML and run the tag derivation shell step without
Docker/GHCR credentials.
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
_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "release.yml"


def _job(name: str) -> dict:
    # YAML parses the bare `on:` key as the boolean `True` under PyYAML's default
    # (YAML 1.1) resolver — irrelevant here since only `jobs` is read.
    return yaml.safe_load(_WORKFLOW_PATH.read_text())["jobs"][name]


def _build_job() -> dict:
    return _job("build")


def _publish_job() -> dict:
    return _job("publish")


def _verify_job() -> dict:
    return _job("verify")


def _wheel_job() -> dict:
    return _job("wheel")


def _uses(step: dict, action: str) -> bool:
    return step.get("uses", "").startswith(f"{action}@")


def _step_index(steps: list[dict], predicate) -> int:
    for i, step in enumerate(steps):
        if predicate(step):
            return i
    raise AssertionError(f"no step matched among: {[s.get('name') or s.get('uses') for s in steps]}")


def test_release_workflow_exists() -> None:
    assert _WORKFLOW_PATH.is_file()


def test_publish_waits_for_build_which_pushes_the_image() -> None:
    """The release page comes last: a failed image build must never leave a
    published Release advertising an image that does not exist."""
    assert _publish_job()["needs"] == ["build"]
    assert any("build-push-action" in str(s.get("uses", "")) for s in _build_job()["steps"])
    assert not any("build-push-action" in str(s.get("uses", "")) for s in _publish_job()["steps"])


def test_image_build_push_step_pushes_and_targets_both_platforms() -> None:
    steps = _build_job()["steps"]
    build_push = steps[_step_index(steps, lambda s: "build-push-action" in str(s.get("uses", "")))]
    with_ = build_push.get("with", {})
    assert with_.get("push") is True
    platforms = str(with_.get("platforms", ""))
    assert "linux/amd64" in platforms
    assert "linux/arm64" in platforms


def test_each_job_holds_exactly_its_own_scopes() -> None:
    """A job-level block replaces the workflow-level one, so each is pinned whole."""
    assert _build_job()["permissions"] == {
        "contents": "read",
        "packages": "write",
        "id-token": "write",
        "attestations": "write",
    }
    assert _publish_job()["permissions"] == {"contents": "write"}
    assert _verify_job()["permissions"] == {"contents": "read"}
    assert _wheel_job()["permissions"] == {"contents": "read"}
    assert _job("rehearsal")["permissions"] == {"contents": "read"}


def test_version_tag_check_runs_before_anything_is_built() -> None:
    """The version/tag agreement check must fail fast, before the
    wheel or image build spends any CI time on a release that can't publish."""
    steps = _wheel_job()["steps"]
    check_idx = _step_index(steps, lambda s: "check-version-tag.sh" in str(s.get("run", "")))
    wheel_build_idx = _step_index(steps, lambda s: "build-wheel.sh" in str(s.get("run", "")))
    assert check_idx < wheel_build_idx


@pytest.mark.parametrize("tag", ["v0.2.0-rc.1", "v0.2.0"])
def test_release_wheel_stamps_the_full_tag_version(tmp_path, tag: str) -> None:
    steps = _wheel_job()["steps"]
    derive = steps[_step_index(steps, lambda s: s.get("id") == "ver")]
    wheel = steps[_step_index(steps, lambda s: str(s.get("run", "")).strip() == "./scripts/build-wheel.sh")]
    assert wheel["env"]["BLIZZARD_VERSION"] == "${{ steps.ver.outputs.version }}"
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", "-e", "-c", derive["run"]],
        env={**os.environ, "GITHUB_REF_NAME": tag, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert f"version={tag[1:]}" in output.read_text().splitlines()
    assert f"prerelease={'true' if '-rc.' in tag else 'false'}" in output.read_text().splitlines()


def test_the_prerelease_flag_publish_reads_comes_from_the_wheel_job() -> None:
    assert _wheel_job()["outputs"]["prerelease"] == "${{ steps.ver.outputs.prerelease }}"
    assert _build_job()["outputs"]["prerelease"] == "${{ needs.wheel.outputs.prerelease }}"
    publish_env = next(s for s in _publish_job()["steps"] if "run" in s)["env"]
    assert publish_env["PRERELEASE"] == "${{ needs.build.outputs.prerelease }}"


def test_wheel_hands_the_wheel_and_its_requirements_to_rehearsal_and_build() -> None:
    steps = _wheel_job()["steps"]
    upload = steps[_step_index(steps, lambda s: _uses(s, "actions/upload-artifact"))]
    assert upload["with"]["name"] == "release-wheel"
    assert upload["with"]["path"].split() == ["dist/*.whl", "dist/*-requirements*.txt"]
    assert _step_index(steps, lambda s: "build-wheel.sh" in str(s.get("run", ""))) < steps.index(upload)


def test_build_publishes_the_rehearsed_wheel_rather_than_building_another() -> None:
    steps = _build_job()["steps"]
    assert not any("build-wheel.sh" in str(s.get("run", "")) for s in steps)
    fetch = _step_index(steps, lambda s: _uses(s, "actions/download-artifact") and s["with"]["name"] == "release-wheel")
    push = _step_index(steps, lambda s: "build-push-action" in str(s.get("uses", "")))
    assert steps[fetch]["with"]["path"] == "dist"
    assert fetch < push


def test_the_rehearsal_runs_on_a_candidate_tag_after_the_wheel_against_the_releases_before_it() -> None:
    job = _job("rehearsal")
    assert job["uses"] == "./.github/workflows/rehearsal.yml"
    assert job["if"] == "contains(github.ref_name, '-rc.')"
    assert job["needs"] == ["wheel"]
    assert job["with"] == {"candidate_artifact": "release-wheel", "tag": "${{ github.ref_name }}"}


def test_build_waits_for_the_rehearsal_and_runs_past_it_only_when_skipped() -> None:
    build = _build_job()
    assert build["needs"] == ["gate", "compat", "service-tier", "crash-sweep", "e2e", "wheel", "rehearsal"]
    assert build["if"] == "${{ !cancelled() && !failure() }}"


def test_publish_and_verify_follow_only_a_successful_predecessor() -> None:
    assert _publish_job()["if"] == "${{ !cancelled() && needs.build.result == 'success' }}"
    assert _verify_job()["if"] == "${{ !cancelled() && needs.publish.result == 'success' }}"


def test_release_notes_are_generated_not_auto_generated() -> None:
    """The published notes come from the tag-tree renderer, including its flag summaries."""
    steps = _build_job()["steps"]
    notes_gen = steps[_step_index(steps, lambda s: "blizzard-change-notes render" in str(s.get("run", "")))]
    assert 'render "${GITHUB_REF_NAME}" > release-notes.md' in notes_gen["run"]
    publish_steps = _publish_job()["steps"]
    publish_run = str(
        publish_steps[_step_index(publish_steps, lambda s: "gh release create" in str(s.get("run", "")))]["run"]
    )
    assert "--notes-file release-notes.md" in publish_run
    assert "--generate-notes" not in publish_run


def test_build_job_waits_for_each_full_suite_tier() -> None:
    """A tag release must not publish ahead of the full suite's three parallel
    jobs (service tier, FULL crash sweep, e2e) — `gate` alone isn't sufficient
    for a release the way it is for the master dev channel."""
    needs = _build_job()["needs"]
    for job in ("gate", "service-tier", "crash-sweep", "e2e"):
        assert job in needs


def test_tags_are_derived_from_the_image_tags_script_not_hardcoded() -> None:
    """The semver fan-out logic lives in scripts/image-tags.sh (unit-tested in
    tests/test_image_tags.py) — the workflow step must consume its output, not
    re-implement the fan-out inline."""
    steps = _build_job()["steps"]
    derive_idx = _step_index(steps, lambda s: "image-tags.sh" in str(s.get("run", "")))
    build_push_idx = _step_index(steps, lambda s: "build-push-action" in str(s.get("uses", "")))
    assert derive_idx < build_push_idx
    build_push = steps[build_push_idx]
    tags_input = str(build_push.get("with", {}).get("tags", ""))
    assert "image_meta" in tags_input, "the build-push step must consume the derived tag list, not a literal"


def _workflow() -> dict:
    return yaml.safe_load(_WORKFLOW_PATH.read_text())


def test_workflow_level_permissions_are_read_only() -> None:
    """The test jobs inherit the workflow level, so it must grant no write scope."""
    assert _workflow()["permissions"] == {"contents": "read"}


def test_only_the_publish_job_holds_contents_write() -> None:
    for name, job in _workflow()["jobs"].items():
        if name == "publish":
            continue
        granted = job.get("permissions") or {}
        assert granted.get("contents") != "write", f"job {name!r} holds contents: write; only `publish` publishes"


def test_the_image_is_signed_then_attested_by_the_push_digest_before_the_inventory() -> None:
    steps = _build_job()["steps"]
    push_idx = _step_index(steps, lambda s: "build-push-action" in str(s.get("uses", "")))
    push_id = steps[push_idx]["id"]
    digest = f"${{{{ steps.{push_id}.outputs.digest }}}}"
    sign_idx = _step_index(steps, lambda s: "cosign sign --yes --recursive" in str(s.get("run", "")))
    assert digest in str(steps[sign_idx]["env"].values())
    image_attest_idx = _step_index(
        steps, lambda s: _uses(s, "actions/attest-build-provenance") and "subject-digest" in s.get("with", {})
    )
    image_attest = steps[image_attest_idx]["with"]
    assert image_attest["subject-digest"] == digest
    assert image_attest["push-to-registry"] is True
    inventory_idx = _step_index(steps, lambda s: "release-inventory.sh" in str(s.get("run", "")))
    assert digest in str(steps[inventory_idx]["env"].values())
    file_attest_idx = _step_index(
        steps, lambda s: _uses(s, "actions/attest-build-provenance") and "subject-path" in s.get("with", {})
    )
    upload_idx = _step_index(steps, lambda s: _uses(s, "actions/upload-artifact"))
    assert push_idx < sign_idx < image_attest_idx < inventory_idx < file_attest_idx < upload_idx
    assert steps[file_attest_idx]["with"]["subject-path"] == "inventory/*"


def test_the_bills_of_materials_are_made_and_attested_after_the_push_before_the_inventory() -> None:
    steps = _build_job()["steps"]
    push_idx = _step_index(steps, lambda s: "build-push-action" in str(s.get("uses", "")))
    inventory_idx = _step_index(steps, lambda s: "release-inventory.sh" in str(s.get("run", "")))
    scans = [i for i, s in enumerate(steps) if _uses(s, "anchore/sbom-action")]
    attests = [i for i, s in enumerate(steps) if _uses(s, "actions/attest-sbom")]
    assert len(scans) == 3 and len(attests) == 3, "one bill of materials per image platform, plus the wheel's"
    assert all(push_idx < i < inventory_idx for i in scans + attests)
    for i in scans:
        with_ = steps[i]["with"]
        assert with_["format"] == "spdx-json"
        assert with_["output-file"].startswith("inventory/")
        assert with_["upload-release-assets"] is False, "only `publish` writes the Release"
    for arch in ("amd64", "arm64"):
        digest = f"${{{{ steps.platform_digests.outputs.{arch} }}}}"
        scan = next(steps[i] for i in scans if str(steps[i]["with"].get("image", "")).endswith(f"@{digest}"))
        assert scan["with"]["image"].startswith("ghcr.io/paul-gross/blizzard-hub@"), (
            "a bare reference: given registry credentials, the action prefixes `registry:` itself"
        )
        attest = next(steps[i]["with"] for i in attests if steps[i]["with"].get("subject-digest") == digest)
        assert attest["push-to-registry"] is True
        assert attest["sbom-path"] == scan["with"]["output-file"]
    wheel_scan = next(steps[i] for i in scans if steps[i]["with"].get("path") == "sbom-wheel")
    wheel_attest = next(steps[i]["with"] for i in attests if "subject-path" in steps[i]["with"])
    assert wheel_attest["subject-path"] == "dist/blizzard-*.whl"
    assert wheel_attest["sbom-path"] == wheel_scan["with"]["output-file"]


def test_the_wheel_bill_of_materials_scans_a_hashed_venv_and_the_board_lockfile() -> None:
    steps = _build_job()["steps"]
    stage = str(steps[_step_index(steps, lambda s: "sbom-wheel/venv" in str(s.get("run", "")))]["run"])
    assert "--require-hashes" in stage
    assert "web/package-lock.json" in stage


def test_the_compat_job_runs_before_any_build_with_read_only_scope() -> None:
    job = _job("compat")
    assert job["name"] == "compatibility against supported releases"
    assert job["permissions"] == {"contents": "read"}
    assert job["steps"][0]["with"]["fetch-depth"] == 0
    assert any(s.get("run") == 'uv run blizzard-compat --tag "${GITHUB_REF_NAME#v}"' for s in job["steps"])
    upload = next(s for s in job["steps"] if _uses(s, "actions/upload-artifact"))
    assert re.fullmatch(r".+@[0-9a-f]{40}", upload["uses"])
    assert upload["with"]["path"] == "compat-breaks.json"
    assert "compat" in _build_job()["needs"]


def test_build_fetches_the_compat_report_to_the_root_before_the_notes() -> None:
    steps = _build_job()["steps"]
    download = _step_index(
        steps, lambda s: _uses(s, "actions/download-artifact") and s["with"]["name"] == "compat-breaks"
    )
    notes = _step_index(steps, lambda s: "change-notes render" in str(s.get("run", "")))
    inventory = _step_index(steps, lambda s: "release-inventory.sh" in str(s.get("run", "")))
    assert steps[download]["with"] == {"name": "compat-breaks", "path": "."}
    assert download < notes < inventory


def test_the_compat_breaks_path_is_always_passed_to_the_inventory_script() -> None:
    steps = _build_job()["steps"]
    run = str(steps[_step_index(steps, lambda s: "release-inventory.sh" in str(s.get("run", "")))]["run"])
    assert "--compat-breaks compat-breaks.json" in run


def test_the_upload_preserves_the_inventory_directory_for_the_publish_glob() -> None:
    """Two upload paths with a common ancestor at the workspace root keep the
    `inventory/` prefix, so the download into `.` yields what `inventory/*` globs."""
    steps = _build_job()["steps"]
    upload = steps[_step_index(steps, lambda s: _uses(s, "actions/upload-artifact"))]["with"]
    assert upload["path"].split() == ["inventory/", "release-notes.md"]
    download_steps = _publish_job()["steps"]
    download = download_steps[_step_index(download_steps, lambda s: _uses(s, "actions/download-artifact"))]["with"]
    assert download["name"] == upload["name"]
    assert download["path"] == "."


def test_publish_attaches_the_inventory_in_one_call_with_the_notes_file() -> None:
    steps = _publish_job()["steps"]
    runs = [str(s.get("run", "")) for s in steps if "gh release create" in str(s.get("run", ""))]
    assert len(runs) == 1
    assert runs[0].count("gh release create") == 1
    assert "inventory/*" in runs[0]
    assert "--notes-file release-notes.md" in runs[0]
    assert "--generate-notes" not in runs[0]
    assert not any(s.get("uses", "").startswith("actions/checkout@") for s in steps)


def test_verify_runs_last_and_proves_the_tag_it_just_published() -> None:
    job = _verify_job()
    assert job["needs"] == ["publish"]
    assert not job.get("outputs")
    assert job["env"]["GH_TOKEN"] == "${{ github.token }}"
    runs = [str(s.get("run", "")) for s in job["steps"] if "verify-release.sh" in str(s.get("run", ""))]
    assert any('./verify-release.sh "${GITHUB_REF_NAME}"' in run for run in runs)


def test_verify_holds_only_the_script_not_a_checkout() -> None:
    steps = _verify_job()["steps"]
    checkout = steps[_step_index(steps, lambda s: _uses(s, "actions/checkout"))]["with"]
    assert checkout["sparse-checkout"] == "scripts/verify-release.sh"
    assert checkout["persist-credentials"] is False
    run = steps[_step_index(steps, lambda s: './verify-release.sh "${GITHUB_REF_NAME}"' in str(s.get("run", "")))]
    assert run["working-directory"] == "${{ runner.temp }}"


@pytest.mark.parametrize(
    "action",
    [
        "sigstore/cosign-installer",
        "actions/attest-build-provenance",
        "actions/attest-sbom",
        "anchore/sbom-action",
        "actions/upload-artifact",
        "actions/download-artifact",
    ],
)
def test_signing_and_artifact_actions_are_pinned_by_commit_sha(action: str) -> None:
    uses = [
        step["uses"] for name in ("wheel", "build", "publish") for step in _job(name)["steps"] if _uses(step, action)
    ]
    assert uses, f"no step uses {action}"
    for ref in uses:
        assert re.fullmatch(rf"{re.escape(action)}@[0-9a-f]{{40}}", ref), ref


def test_every_action_in_verify_is_pinned_by_commit_sha() -> None:
    uses = [step["uses"] for step in _verify_job()["steps"] if "uses" in step]
    assert len(uses) == 3
    for ref in uses:
        assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref


_WORKFLOWS_DIR = _REPO_ROOT / ".github" / "workflows"
_MOCK_ACTION = "./blizzard/.github/actions/resolve-mock-ref"
_TIER_JOBS = {
    "upper-tiers.yml": ("service-tier", "crash-sweep"),
    "release.yml": ("service-tier", "crash-sweep", "e2e"),
}


def _tier_job(workflow: str, name: str) -> dict:
    return yaml.safe_load((_WORKFLOWS_DIR / workflow).read_text())["jobs"][name]


def _tier_jobs():
    return [(workflow, job) for workflow, jobs in _TIER_JOBS.items() for job in jobs]


@pytest.mark.parametrize(("workflow", "job"), _tier_jobs())
def test_a_tier_job_resolves_the_mock_through_the_action_after_checking_blizzard_out(workflow: str, job: str) -> None:
    steps = _tier_job(workflow, job)["steps"]
    checkout = _step_index(steps, lambda s: _uses(s, "actions/checkout") and s["with"].get("path") == "blizzard")
    resolve = _step_index(steps, lambda s: s.get("uses") == _MOCK_ACTION)
    assert checkout < resolve


@pytest.mark.parametrize("workflow", sorted(p.name for p in _WORKFLOWS_DIR.glob("*.yml")))
def test_no_step_checks_the_mock_out_directly(workflow: str) -> None:
    for job in yaml.safe_load((_WORKFLOWS_DIR / workflow).read_text())["jobs"].values():
        for step in job.get("steps") or []:
            assert (step.get("with") or {}).get("repository") != "paul-gross/blizzard-mock", step


@pytest.mark.parametrize("job", _TIER_JOBS["upper-tiers.yml"])
def test_the_upper_tier_jobs_check_out_the_commit_mock_ref_resolved(job: str) -> None:
    spec = _tier_job("upper-tiers.yml", job)
    assert "mock-ref" in spec["needs"]
    step = next(s for s in spec["steps"] if s.get("uses") == _MOCK_ACTION)
    assert step["with"]["sha"] == "${{ needs.mock-ref.outputs.sha }}"


def test_upper_tiers_exposes_the_resolved_mock_commit() -> None:
    workflow = yaml.safe_load((_WORKFLOWS_DIR / "upper-tiers.yml").read_text())
    assert workflow[True]["workflow_call"]["outputs"]["mock_sha"]["value"] == "${{ jobs.mock-ref.outputs.sha }}"
    assert workflow["jobs"]["mock-ref"]["outputs"]["sha"] == "${{ steps.mock.outputs.sha }}"


@pytest.mark.parametrize("job", _TIER_JOBS["release.yml"])
def test_a_tag_run_resolves_the_tag_itself(job: str) -> None:
    step = next(s for s in _tier_job("release.yml", job)["steps"] if s.get("uses") == _MOCK_ACTION)
    assert "sha" not in (step.get("with") or {})
