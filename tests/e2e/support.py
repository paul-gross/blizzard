"""Shared helpers for end-to-end scenarios."""

from __future__ import annotations

import os
import time

import httpx2
import yaml

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop_wiring import LoopWiring
from tests.fleet_launchers import runner_api
from tests.fleet_world import PUSH_AND_DECLARE_SCRIPT, REPO_NAME, scripted_build_prompt
from tests.runner_fakes import daemon_store

_BUILD_SCRIPT = scripted_build_prompt(
    "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    'p = pathlib.Path(repo) / "BUILD.md"\n'
    'p.write_text((p.read_text() if p.exists() else "") + "build pass\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: a build pass"],\n'
    "    check=True,\n"
    ")\n" + PUSH_AND_DECLARE_SCRIPT
)
_BUILD_JUDGEMENT = "verdict('pass', 'checks are green')\n"
_REVIEW_SCRIPT = "pass\n"
_REVIEW_JUDGEMENT = (
    "import pathlib\n"
    "m = pathlib.Path('.review-count')\n"
    "n = (int(m.read_text()) if m.exists() else 0) + 1\n"
    "m.write_text(str(n))\n"
    "if n == 1:\n"
    "    verdict('fail', 'BLOCKING: guard the empty input before delivery')\n"
    "else:\n"
    "    verdict('pass', 'findings addressed; ready to deliver')\n"
)


def _graph_yaml() -> str:
    """A build-review-deliver graph exercising node session modes."""

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _BUILD_SCRIPT,
                "session": "resume:build",
                "judgement": {
                    "prompt": _BUILD_JUDGEMENT,
                    "choices": {
                        "pass": {"description": "Committed and green.", "to": "review"},
                        "fail": {"description": "Incomplete.", "to": "build"},
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "review": {
                "executor": "runner",
                "prompt": _REVIEW_SCRIPT,
                "session": "fresh",
                "produces": ["review-findings"],
                "judgement": {
                    "prompt": _REVIEW_JUDGEMENT,
                    "choices": {
                        "pass": {"description": "Passes review.", "to": "deliver"},
                        "fail": {"description": "Blocking issues found.", "to": "build"},
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "action": "land",
                "poll_interval": 1,
                "judgement": {
                    "choices": {
                        "landed": {"description": "Landed.", "to": "done"},
                        "conflict": {"description": "Conflict.", "to": "build"},
                        "failure": {"description": "Failed.", "to": "build"},
                        "inherited-failure": {"description": "The base check stays red.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _sessions_by_node(db_url: str, chunk_id: str) -> dict[str, list[str]]:
    store = daemon_store(create_engine_from_url(db_url))
    leases = [store.lease(lid) for lid in store.lease_ids_for_chunk(chunk_id)]
    ordered = sorted((lz for lz in leases if lz is not None), key=lambda lz: lz.created_at)
    by_node: dict[str, list[str]] = {}
    for lz in ordered:
        if lz.session_id is not None:
            by_node.setdefault(lz.node_name, []).append(lz.session_id)
    return by_node


def _tick_until(
    config: RunnerConfig,
    hub: httpx2.Client,
    chunk_id: str,
    fenced: dict[str, str],
    targets: set[str],
    timeout: float,
    process=None,
) -> str:
    prior = dict(os.environ)
    os.environ.update(fenced)
    try:
        deadline = time.monotonic() + timeout
        status = "ready"
        while time.monotonic() < deadline:
            LoopWiring.of(config).tick_once(process=process)
            status = hub.get(f"/api/chunks/{chunk_id}").json()["status"]
            if status in targets:
                return status
            time.sleep(0.5)
        return status
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _drive_until(config: RunnerConfig, hub: httpx2.Client, chunk_id: str, env: dict[str, str], predicate, timeout=60.0):
    prior = dict(os.environ)
    os.environ.update(env)
    try:
        with runner_api(config):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                LoopWiring.of(config).tick_once()
                detail = hub.get(f"/api/chunks/{chunk_id}")
                assert detail.status_code == 200, detail.text
                body = detail.json()
                if predicate(body):
                    return body
                time.sleep(0.4)
            raise AssertionError("predicate never became true within the timeout")
    finally:
        os.environ.clear()
        os.environ.update(prior)
