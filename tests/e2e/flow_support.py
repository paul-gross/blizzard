"""Shared ground for the flow e2e modules: chunks pinned to a graph that lands them, a runner ticked until the line
holds, and the mock forge's ``checks_pending`` lever that keeps a chunk pending at ``deliver``."""

from __future__ import annotations

import dataclasses
import os
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

import httpx2
import pytest
import yaml

from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop_wiring import LoopWiring
from tests.fleet_launchers import REPO, mock_bin_dir, runner_api, runner_config
from tests.fleet_world import FIXTURE_ENV, REPO_NAME, basic_workspace, mint_world

LAND_CHOICES = {
    "landed": {"description": "Every repo's PR merged cleanly.", "to": "done"},
    "conflict": {"description": "A repo's PR is dirty; back to build.", "to": "build"},
    "failure": {"description": "A repo could not land; back to build.", "to": "build"},
    "inherited-failure": {"description": "The base check stays red; back to build.", "to": "build"},
}
_RECORD_FINDINGS = {
    "executor": "hub",
    "action": "record-findings",
    "judgement": {
        "choices": {
            "recorded": {"description": "The deferred findings are durable.", "to": "done"},
            "invalid": {"description": "The delta failed validation.", "to": "done"},
            "failure": {"description": "The record was refused.", "to": "done"},
        }
    },
}
_PUBLISH_DELTA = (
    "subprocess.run(['blizzard', 'runner', 'artifact', 'create', '--name', 'review-finding-delta'],\n"
    '               input=\'{"entries": [{"ref": "F1", "disposition": "fixed"}]}\', text=True, check=True)\n'
)


def _build_script(landed_file: str, repos: Sequence[str], *, delta: bool) -> str:
    """A build that commits ``landed_file`` in each of ``repos`` on a branch named for it, pushes, and declares it —
    then, with ``delta``, publishes the review-finding delta ``record-findings`` reads."""
    branch = f"feature/{Path(landed_file).stem.lower()}"
    return (
        "import os, pathlib, subprocess\n"
        "root = pathlib.Path(os.environ['BLIZZARD_ENV_WORKDIRS'].split(',')[0])\n"
        f"for name in {list(repos)!r}:\n"
        "    path = str(root / name)\n"
        f"    subprocess.run(['git', '-C', path, 'switch', '-C', {branch!r}], check=True)\n"
        f"    (root / name / {landed_file!r}).write_text('held on a flow\\n')\n"
        "    subprocess.run(['git', '-C', path, 'add', '-A'], check=True)\n"
        "    subprocess.run(['git', '-C', path, '-c', 'user.email=mock@blizzard.local',\n"
        f"                    '-c', 'user.name=Mock Harness', 'commit', '-m', 'feat: {landed_file}'], check=True)\n"
        "    commit = subprocess.run(['git', '-C', path, 'rev-parse', 'HEAD'],\n"
        "                            check=True, capture_output=True, text=True).stdout.strip()\n"
        f"    subprocess.run(['git', '-C', path, 'push', '--force', 'origin', {branch!r}], check=True)\n"
        "    subprocess.run(['blizzard', 'runner', 'artifact', 'commit', '--repo', name,\n"
        f"                    '--branch', {branch!r}, '--commit', commit], check=True)\n"
    ) + (_PUBLISH_DELTA if delta else "")


def landing_graph_yaml(landed_file: str, repos: Sequence[str], *, poll_interval: int, record_findings: bool) -> str:
    """A graph whose build commits ``landed_file`` in ``repos`` and whose ``deliver`` lands it, re-polling every
    ``poll_interval`` seconds — then ``record-findings`` when asked."""
    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _build_script(landed_file, repos, delta=record_findings),
                "judgement": {
                    "prompt": "verdict('pass', 'the mock harness did its turn')\n",
                    "choices": {"pass": {"description": "Built.", "to": "deliver"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "action": "land",
                "poll_interval": poll_interval,
                "poll_timeout": 600,
                "judgement": {"choices": LAND_CHOICES},
            },
        },
    }
    if record_findings:
        graph["nodes"]["deliver"]["judgement"] = {
            "choices": {**LAND_CHOICES, "landed": {**LAND_CHOICES["landed"], "to": "record-findings"}}
        }
        graph["nodes"]["build"]["produces"] = ["review-finding-delta"]
        graph["nodes"]["record-findings"] = _RECORD_FINDINGS
    return yaml.safe_dump(graph, sort_keys=False)


def pinned_chunk(
    hub: httpx2.Client,
    forge: httpx2.Client,
    landed_file: str,
    repos: Sequence[str] = (REPO_NAME,),
    *,
    poll_interval: int = 1,
    record_findings: bool = False,
) -> str:
    """Mint the graph landing ``landed_file`` in ``repos``, ingest one chunk pinned to it, and promote it."""
    graph = landing_graph_yaml(landed_file, repos, poll_interval=poll_interval, record_findings=record_findings)
    assert hub.post("/api/graphs", json={"definition_yaml": graph}).status_code == 201
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": f"lands {landed_file}"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    return chunk_id


@contextmanager
def ticking_runner(config: RunnerConfig) -> Iterator[Callable[[Callable[[], bool]], None]]:
    """A live runner; yields ``tick_until(predicate)``, which ticks it until ``predicate`` holds."""
    prior = dict(os.environ)
    os.environ["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"

    def tick_until(predicate: Callable[[], bool]) -> None:
        deadline = time.monotonic() + 120.0
        while time.monotonic() < deadline:
            LoopWiring.of(config).tick_once()
            if predicate():
                return
            time.sleep(0.4)
        raise AssertionError("the runner never brought the chunks to the awaited state")

    try:
        with runner_api(config):
            yield tick_until
    finally:
        os.environ.clear()
        os.environ.update(prior)


def set_lever(forge: httpx2.Client, repo: str, *, armed: bool) -> None:
    if armed:
        assert forge.post("/_levers/checks_pending", json={"repo": repo}).status_code == 200
    else:
        assert forge.delete("/_levers/checks_pending", params={"repo": repo}).status_code == 200


def flow_world(tmp_path: Path) -> tuple[Path, Path, Path]:
    """The mock bin, a freshly minted fixture workspace, and its forge origins."""
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    mint_world(bin_dir, tmp_path / "scratch")
    fixture_root = tmp_path / "scratch" / FIXTURE_ENV
    return bin_dir, fixture_root / "workspace", fixture_root / "origins"


def flow_runner_config(
    tmp_path: Path, workspace: Path, bin_dir: Path, hub_port: int, *, environments: int = 2
) -> RunnerConfig:
    """A runner with an environment per chunk held at ``deliver`` at once."""
    return dataclasses.replace(
        runner_config(tmp_path / "runner", workspace, bin_dir, hub_port),
        workspaces=(dataclasses.replace(basic_workspace(workspace), max_environments=environments),),
    )
