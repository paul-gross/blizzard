"""The platform-behavior tiers' shared fleet world — winter-free.

The service, e2e, and crash tiers prove platform behavior, not the winter binding: their runners run on the
built-in ``basic`` provider over a ``blizzard-mock`` fixture minted without a winter source (bare ``file://``
origins plus an empty ``workspace/``). This module owns what every such scenario shares — the fixture mint, the
runner's workspace over it, the scripted build prompt that works on a basic worktree, and the scripted
``default-delivery`` graph with its node scripts.
The winter binding's own cases live in ``tests/winter/``.
"""

from __future__ import annotations

import functools
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import pytest

from blizzard.runner.config import DEFAULT_WORKSPACE_ID, HostWorkspace
from blizzard.runner.environments.provider import WorkspaceRepo
from tests.e2e.fleet_traces import PLANT_LEASE_TOKEN_SCRIPT, SENTINEL_ARTIFACT_BODY
from tests.e2e.harness_variants import CLAUDE_CODE, MockHarness

#: The fixture project repo the platform tiers drive and the env keying the disposable fixture world.
REPO_NAME = "toy-api"
FIXTURE_ENV = "e2e"
#: The branch a scripted build works on; a basic worktree arrives detached at ``origin/<base>``.
BUILD_BRANCH = "feature/scripted-build"

_FENCE_FILE = ".blizzard-mock-harness-fence"
_WINTER_SOURCE_VAR = "BLIZZARD_MOCK_WINTER_SOURCE"


class FleetWorld(NamedTuple):
    """One minted fixture world: the runner's workspace root and the bare origins the forge fronts."""

    workspace: Path
    origins: Path
    origin_bare: Path


def winter_free_env() -> dict[str, str]:
    """The ambient environment with no winter source, so no child can bind the fixture to winter."""
    return {key: value for key, value in os.environ.items() if key != _WINTER_SOURCE_VAR}


def mint_world(
    bin_dir: Path, scratch: Path, *, env: str = FIXTURE_ENV, fence: str = "fleet world fence marker\n"
) -> FleetWorld:
    """Mint a fresh, disposable fixture world under ``scratch`` and fence it for the mock harness.

    Idempotent via ``reset``. The mint names no winter source and the child environment carries none."""
    minted = subprocess.run(
        [str(bin_dir / "blizzard-mock-fixture"), "reset", "--env", env, "--scratch-root", str(scratch)],
        capture_output=True,
        text=True,
        env=winter_free_env(),
    )
    if minted.returncode != 0:
        pytest.fail(f"fixture reset exited {minted.returncode}:\n{minted.stdout}\n{minted.stderr}")
    root = scratch / env
    workspace, origins = root / "workspace", root / "origins"
    origin_bare = origins / f"{REPO_NAME}.git"
    assert workspace.is_dir() and origin_bare.is_dir(), "fixture mint did not lay out the expected tree"
    # Fence the tree so the mock harness will run (arbitrary code execution, gated on a marker file).
    (workspace / _FENCE_FILE).write_text(fence)
    return FleetWorld(workspace, origins, origin_bare)


def world_repos(workspace: Path) -> tuple[WorkspaceRepo, ...]:
    """Every seeded origin beside ``workspace`` (``<fixture>/origins/*.git``) as a basic-provider repo."""
    origins = workspace.parent / "origins"
    if not origins.is_dir():
        return ()
    return tuple(WorkspaceRepo(bare.stem, f"file://{bare}") for bare in sorted(origins.glob("*.git")))


def basic_workspace(workspace: Path, *, repos: tuple[WorkspaceRepo, ...] | None = None) -> HostWorkspace:
    """The runner's one workspace over ``workspace`` on the basic provider, one environment at a time."""
    return HostWorkspace(
        DEFAULT_WORKSPACE_ID,
        root=str(workspace),
        provider="basic",
        max_environments=1,
        repos=world_repos(workspace) if repos is None else repos,
        base_branch="main",
    )


def scripted_build_prompt(build: str, *, branch: str = BUILD_BRANCH, reset: bool = True) -> str:
    """``build`` — a scripted build program written against ``repo = 'toy-api'`` — made to run on a basic worktree.

    The worktree arrives detached, so the program first switches to ``branch`` (``reset`` recreates it, which a
    re-run of the node needs), and it
    locates the repo through the injected ``BLIZZARD_ENV_WORKDIRS`` rather than assuming a layout."""
    if "BLIZZARD_ENV_WORKDIRS" in build:
        return build
    repo_path = f"str(pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0]) / {REPO_NAME!r})"
    build = build.replace(f"repo = {REPO_NAME!r}", f"repo = {REPO_NAME!r}\nrepo_path = {repo_path}")
    build = build.replace("pathlib.Path(repo)", "pathlib.Path(repo_path)")
    build = build.replace('["git", "-C", repo', '["git", "-C", repo_path')
    return (
        "import os, pathlib, subprocess\n"
        f"subprocess.run(['git', '-C', {repo_path}, 'switch', {'-C' if reset else '-c'!r}, {branch!r}], "
        "check=True)\n" + build
    )


def scripted_build[**P](script: Callable[P, str]) -> Callable[P, str]:
    """Decorate a function returning a scripted build program so it returns :func:`scripted_build_prompt` of it."""

    @functools.wraps(script)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> str:
        return scripted_build_prompt(script(*args, **kwargs))

    return wrapped


# Appended to a build prompt after a real commit: pushes the branch and declares it via
# `blizzard runner artifact commit`; declaring twice per lease is harmless.
PUSH_AND_DECLARE_SCRIPT = (
    "_branch = subprocess.run(\n"
    '    ["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout.strip()\n"
    "_commit = subprocess.run(\n"
    '    ["git", "-C", repo, "rev-parse", "HEAD"],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout.strip()\n"
    'subprocess.run(["git", "-C", repo, "push", "origin", _branch], check=True, capture_output=True)\n'
    "subprocess.run(\n"
    '    ["blizzard", "runner", "artifact", "commit",\n'
    '     "--repo", repo, "--branch", _branch, "--commit", _commit],\n'
    "    check=True, capture_output=True,\n"
    ")\n"
)

# The scripted build-node prompt: the prompt is the program, run under the mock harness
# with the acquired env dir as cwd, so it targets `toy-api` by relative path.
BUILD_SCRIPT = scripted_build_prompt(
    "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    '(pathlib.Path(repo) / "LANDED.md").write_text("landed by the mock harness\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True, capture_output=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: land a change from the mock harness"],\n'
    "    check=True, capture_output=True,\n"
    ")\n" + PUSH_AND_DECLARE_SCRIPT
)
# The build, probed: it plants its lease token for the leak scan and calls the hub through the runner.
PROBED_BUILD_SCRIPT = (
    PLANT_LEASE_TOKEN_SCRIPT
    + "import os, subprocess\n"
    + 'subprocess.run(["blizzard", "runner", "work-items", os.environ["BLIZZARD_CHUNK_ID"]], check=True, capture_output=True)\n'
    + BUILD_SCRIPT
)
# The judgement-resume prompt: also arrives as code.
JUDGEMENT_SCRIPT = "verdict('pass', 'the mock harness committed the change; checks are green')\n"

# The review node scripted to PASS on first look, so the build commit travels straight to
# deliver with no re-build; the base turn is a no-op, the verdict comes on judgement resume.
REVIEW_SCRIPT = "pass\n"
REVIEW_JUDGEMENT = "verdict('pass', 'cold-eyes review: the committed change is clean; ready to deliver')\n"
# The review, probed: it submits the sentinel body as its findings artifact.
PROBED_REVIEW_SCRIPT = (
    PLANT_LEASE_TOKEN_SCRIPT
    + "import subprocess\n"
    + "subprocess.run(\n"
    + '    ["blizzard", "runner", "artifact", "create", "--name", "review-findings"],\n'
    + f"    input={SENTINEL_ARTIFACT_BODY!r}, text=True, check=True, capture_output=True,\n"
    + ")\n"
)


def graph_yaml(harness: MockHarness = CLAUDE_CODE, *, probed: bool = False) -> str:
    """The scripted ``default-delivery`` graph — ``build -> review -> deliver``.

    Named ``default-delivery`` so the hub's lazy default-graph mint reuses this
    pre-minted graph by name — the packaged prompts are LLM prose the mock cannot ``exec``. ``probed`` swaps in
    the build and review scripts a trace proof reads back.
    """
    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": PROBED_BUILD_SCRIPT if probed else BUILD_SCRIPT,
                "judgement": {
                    "prompt": JUDGEMENT_SCRIPT,
                    "choices": {
                        "pass": {
                            "description": "The change is committed and the node's checks are green.",
                            "to": "review",
                        }
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "review": {
                "executor": "runner",
                "prompt": PROBED_REVIEW_SCRIPT if probed else REVIEW_SCRIPT,
                "session": "fresh",
                "produces": ["review-findings"],
                "judgement": {
                    "prompt": REVIEW_JUDGEMENT,
                    "choices": {
                        "pass": {"description": "The change passes cold-eyes review.", "to": "deliver"},
                        "fail": {
                            "description": "Blocking issues found.",
                            "to": "build",
                            "prompt_addendum": "# address the review findings\n",
                        },
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "run": [{"command": "python3 -m blizzard.hub.graphs.scripts.land_default"}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Landed.", "to": "done"},
                        "conflict": {"description": "Conflict.", "to": "build"},
                        "failure": {"description": "Failed.", "to": "build"},
                    }
                },
            },
        },
    }
    return harness.graph_yaml(graph)
