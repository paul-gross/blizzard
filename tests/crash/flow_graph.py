"""Delivery graph used by the crash sweep."""

from collections.abc import Sequence
from pathlib import Path

import yaml

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


def _graph(landed_file: str, repos: Sequence[str], *, poll_interval: int, record_findings: bool) -> str:
    """Build and land files, optionally recording findings after delivery."""
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
