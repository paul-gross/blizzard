"""The platform tiers and the CI workflows stay winter-free.

The service, e2e, and crash tiers run their runners on the built-in ``basic`` provider over a fixture minted without a
winter source, and the workflows check out only ``blizzard`` and ``blizzard-mock``. The winter binding's own tests are
the local-only ``winter_binding`` tier (``tests/winter/``). A static scan keeps either from drifting back, and pins that
every checkout in a job that runs tests leaves no credentials behind.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_ROOT = repo_root()
_PLATFORM_TIERS = ("service", "e2e", "crash", "journey")
_WORKFLOWS = sorted((_ROOT / ".github" / "workflows").glob("*.yml"))

#: Configures the winter provider or hands a fixture a winter source — what a platform-tier module must never do.
_WINTER_BOUND = re.compile(r"""provider\s*=\s*["']winter["']|--winter-source|winter_source|WINTER_SOURCE""")
#: A step command that runs tests.
_RUNS_TESTS = re.compile(r"pytest|mise run e2e")


def _platform_modules() -> list[Path]:
    return sorted(path for tier in _PLATFORM_TIERS for path in (_ROOT / "tests" / tier).rglob("*.py"))


@pytest.mark.parametrize("module", _platform_modules(), ids=lambda path: str(path.relative_to(_ROOT)))
def test_a_platform_tier_module_is_not_winter_bound(module: Path) -> None:
    hit = _WINTER_BOUND.search(module.read_text())
    assert hit is None, (
        f"{module.relative_to(_ROOT)} binds to winter ({hit and hit.group(0)!r}); use tests/fleet_world.py"
    )


@pytest.mark.parametrize("workflow", _WORKFLOWS, ids=lambda path: path.name)
def test_a_workflow_names_no_winter_source(workflow: Path) -> None:
    text = workflow.read_text()
    for forbidden in ("blizzard-workspace", "BLIZZARD_MOCK_WINTER_SOURCE"):
        assert forbidden not in text, (
            f"{workflow.name} names {forbidden!r}; CI checks out only blizzard and blizzard-mock"
        )


def _local_action_steps(step: dict) -> list[dict]:
    uses = str(step.get("uses", ""))
    if not uses.startswith("./"):
        return []
    relative = uses.removeprefix("./").removeprefix("blizzard/")
    action = yaml.safe_load((_ROOT / relative / "action.yml").read_text())
    return action.get("runs", {}).get("steps") or []


def _test_running_checkouts() -> list[tuple[str, str, dict]]:
    found = []
    for workflow in _WORKFLOWS:
        for job_name, job in yaml.safe_load(workflow.read_text()).get("jobs", {}).items():
            steps = [inner for step in job.get("steps") or [] for inner in (step, *_local_action_steps(step))]
            if not any(_RUNS_TESTS.search(str(step.get("run", ""))) for step in steps):
                continue
            found += [
                (workflow.name, job_name, step)
                for step in steps
                if str(step.get("uses", "")).startswith("actions/checkout")
            ]
    return found


def test_the_scan_follows_the_mock_checkout_into_the_composite_action() -> None:
    mock_checkouts = [
        step
        for _, _, step in _test_running_checkouts()
        if (step.get("with") or {}).get("repository") == "paul-gross/blizzard-mock"
    ]
    assert len(mock_checkouts) >= 5


def test_the_scan_sees_the_test_running_jobs() -> None:
    assert {(name, job) for name, job, _ in _test_running_checkouts()} >= {
        ("gate.yml", "python-tests"),
        ("upper-tiers.yml", "service-tier"),
        ("upper-tiers.yml", "crash-sweep"),
        ("upper-tiers.yml", "e2e"),
        ("upper-tiers.yml", "journey"),
    }


@pytest.mark.parametrize(
    ("workflow", "job", "step"), _test_running_checkouts(), ids=lambda value: value if isinstance(value, str) else ""
)
def test_a_test_running_checkout_does_not_persist_credentials(workflow: str, job: str, step: dict) -> None:
    assert (step.get("with") or {}).get("persist-credentials") is False, (
        f"{workflow}:{job} checks out {step.get('name') or step.get('with')} without persist-credentials: false"
    )
