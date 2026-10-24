"""A store held before hub nodes named actions carries over, and every chunk on it lands with its script's choice.

A HEAD hub pins a chunk to one version per shipped ``run:`` command and an empty ``run:``, the ``land_pr_ci`` one left
mid-``pending``; the store walks down to ``20261023_0901_hub_actions``, pinned SQL rewrites each hub node to the
``run:`` row it was minted as, and the HEAD migrate and hub carry it. Needs ``BLIZZARD_E2E=1`` and ``blizzard-mock``.
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx2
import pytest
import yaml

from tests.e2e.test_delivery_pr_ci_e2e import _drive_until
from tests.fleet_launchers import REPO, drive_until_done, mock_bin_dir, run_forge, run_hub, runner_config
from tests.fleet_world import FIXTURE_ENV, PUSH_AND_DECLARE_SCRIPT, REPO_NAME, mint_world, scripted_build_prompt
from tests.support import free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e run carry-over needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

_PARENT = "20261023_0901_hub_actions"
_MODULES = "blizzard.hub.graphs.scripts."
_SCRIPTS = f"python3 -m {_MODULES}"
_GARDEN_GRAPH = "carried-garden"
_GARDEN_SCOPE = "carried-scope"


def _committing_build(landed_file: str) -> str:
    return scripted_build_prompt(
        "import subprocess, pathlib\n"
        f"repo = {REPO_NAME!r}\n"
        f'(pathlib.Path(repo) / {landed_file!r}).write_text("carried over\\n")\n'
        'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
        "subprocess.run(\n"
        '    ["git", "-C", repo,\n'
        '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
        f'     "commit", "-m", "feat: {landed_file}"],\n'
        "    check=True,\n"
        ")\n" + PUSH_AND_DECLARE_SCRIPT
    )


def _publish(name: str, content: object) -> str:
    return (
        "import subprocess\n"
        f"subprocess.run(['blizzard', 'runner', 'artifact', 'create', '--name', {name!r}],\n"
        f"               input={json.dumps(content)!r}, text=True, check=True)\n"
    )


_REVIEW_BUILD = _publish("review-finding-delta", {"entries": [{"ref": "F1", "disposition": "fixed"}]})
_GARDEN_SWEEP = (
    "import subprocess\n"
    f"rev = subprocess.run(['git', '-C', {REPO_NAME!r}, 'rev-parse', 'HEAD'],\n"
    "                     check=True, capture_output=True, text=True).stdout.strip()\n"
    "import json\n"
    "for name, content in (\n"
    f"    ('delta', {{'scope': {_GARDEN_SCOPE!r}, 'revisions': {{{REPO_NAME!r}: rev}},\n"
    "               'measurement': 'a carried-over sweep', 'findings': []}),\n"
    "    ('docket', []),\n"
    "):\n"
    "    subprocess.run(['blizzard', 'runner', 'artifact', 'create', '--name', name],\n"
    "                   input=json.dumps(content), text=True, check=True)\n"
)

_LAND_CHOICES = {
    "landed": {"description": "Every repo's PR merged cleanly.", "to": "done"},
    "conflict": {"description": "A repo's PR is dirty; back to build.", "to": "build"},
    "failure": {"description": "A repo could not land; back to build.", "to": "build"},
    "inherited-failure": {"description": "The base check stays red; back to build.", "to": "build"},
}
_DELIVERY_CHOICES = {
    "recorded": {"description": "Recorded.", "to": "done"},
    "invalid": {"description": "Refused; back to build.", "to": "build"},
    "failure": {"description": "The pass failed; back to build.", "to": "build"},
}


class _Version:
    """One graph version a pre-action installation held: its hub node's ``run:`` and the action that run names."""

    def __init__(
        self,
        label: str,
        command: str | None,
        action: str,
        choices: dict[str, Any],
        build: str,
        *,
        name: str = "default-delivery",
        with_: dict[str, str] | None = None,
        produces: tuple[str, ...] = (),
    ) -> None:
        self.label, self.command, self.action, self.choices, self.build = label, command, action, choices, build
        self.name, self.with_, self.produces = name, with_, produces

    def steps(self) -> list[dict[str, Any]]:
        return [] if self.command is None else [{"name": self.label, "command": self.command, "produces": None}]

    def _document(self, hub_node: dict[str, Any]) -> str:
        build = {
            "executor": "runner",
            "prompt": self.build,
            **({"produces": list(self.produces)} if self.produces else {}),
            "judgement": {
                "prompt": "verdict('pass', 'the mock harness did its turn')\n",
                "choices": {"pass": {"description": "Built.", "to": "deliver"}},
            },
            "retries": {"max": 1, "exhausted": "escalate"},
        }
        deliver = {
            **hub_node,
            **({"poll_interval": 1, "poll_timeout": 600} if self.action == "land" else {}),
            "judgement": {"choices": self.choices},
        }
        graph = {"name": self.name, "entry": "build", "nodes": {"build": build, "deliver": deliver}}
        return yaml.safe_dump(graph, sort_keys=False)

    def minted(self) -> str:
        return self._document(
            {"executor": "hub", "action": self.action, **({"with": self.with_} if self.with_ else {})}
        )

    def stored(self) -> str:
        """The document as minted before actions: the node names its ``run:``, never an action."""
        steps = [{"name": s["name"], "command": s["command"]} for s in self.steps()]
        return self._document({"executor": "hub", "run": steps})


_VERSIONS = (
    _Version("land_pr_ci", f"{_SCRIPTS}land_pr_ci", "land", _LAND_CHOICES, _committing_build("PR_CI.md")),
    _Version("land_default", f"{_SCRIPTS}land_default", "land", _LAND_CHOICES, _committing_build("DEFAULT.md")),
    _Version("land_ff", f"  python  -m {_MODULES}land_ff ", "land", _LAND_CHOICES, _committing_build("FF.md")),
    _Version(
        "review_deliver",
        f"{_SCRIPTS}review_deliver",
        "record-findings",
        _DELIVERY_CHOICES,
        _REVIEW_BUILD,
        produces=("review-finding-delta",),
    ),
    _Version(
        "empty",
        None,
        "pass",
        {
            "success": {"description": "Routed.", "to": "done"},
            "failure": {"description": "The pass failed; back to build.", "to": "build"},
        },
        "pass\n",
    ),
    _Version(
        "garden_deliver",
        f"{_SCRIPTS}garden_deliver --delta delta --proposals docket",
        "garden-deliver",
        _DELIVERY_CHOICES,
        _GARDEN_SWEEP,
        name=_GARDEN_GRAPH,
        with_={"delta": "delta", "proposals": "docket"},
        produces=("delta", "docket"),
    ),
)
_PENDING = "land_pr_ci"


def _ingest(hub: httpx2.Client, forge: httpx2.Client, label: str) -> str:
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": label, "body": f"a chunk pinned to {label}"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
    assert ingested.status_code == 201, ingested.text
    return ingested.json()["chunk_id"]


def _routine_run(hub: httpx2.Client) -> str:
    created = hub.post(
        "/api/routines",
        json={
            "name": "carried-routine",
            "graph_name": _GARDEN_GRAPH,
            "default_scope_slug": _GARDEN_SCOPE,
            "default_model": [],
            "default_effort": None,
        },
    )
    assert created.status_code == 201, created.text
    run = hub.post(
        f"/api/routines/{created.json()['routine_id']}/run", json={"scope_slug": _GARDEN_SCOPE, "mode": "full"}
    )
    assert run.status_code == 201, run.text
    return run.json()["chunk_id"]


def _pin_each_version(hub: httpx2.Client, forge: httpx2.Client) -> dict[str, tuple[str, str]]:
    """Mint each version and pin one chunk to it; answer each label's graph id and chunk id."""
    pinned: dict[str, tuple[str, str]] = {}
    for version in _VERSIONS:
        minted = hub.post("/api/graphs", json={"definition_yaml": version.minted()})
        assert minted.status_code == 201, minted.text
        chunk_id = _routine_run(hub) if version.name == _GARDEN_GRAPH else _ingest(hub, forge, version.label)
        assert hub.get(f"/api/chunks/{chunk_id}").json()["graph_id"] == minted.json()["graph_id"]
        pinned[version.label] = (minted.json()["graph_id"], chunk_id)
    return pinned


def _store(hub_dir: Path) -> sqlite3.Connection:
    return sqlite3.connect(hub_dir / "data" / "hub.db")


def _held_before_actions(hub_dir: Path, pinned: dict[str, tuple[str, str]]) -> None:
    """Rewrite each version's hub node to the row a pre-action installation held, at the parent revision."""
    with _store(hub_dir) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone() == (_PARENT,)
        for version in _VERSIONS:
            graph_id = pinned[version.label][0]
            conn.execute(
                "UPDATE graph_nodes SET action = NULL, action_args = NULL, run = ?"
                " WHERE graph_id = ? AND executor = 'hub'",
                (json.dumps(version.steps()), graph_id),
            )
            conn.execute("UPDATE graphs SET definition_yaml = ? WHERE graph_id = ?", (version.stored(), graph_id))


def _hub_migrate(hub_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """``blizzard-hub migrate``, as the packaged unit's start step and the image's entrypoint run it."""
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    done = subprocess.run([hub_bin, "migrate", "--dir", str(hub_dir), *args], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done


def _choices(detail: dict[str, Any]) -> list[str]:
    return [h["choice_name"] for h in detail["history"] if h.get("from_node_name") == "deliver"]


def _artifact_names(detail: dict[str, Any]) -> set[str]:
    return {a["name"] for a in detail["artifacts"]}


def test_a_store_held_before_actions_carries_over_and_lands_every_chunk(tmp_path: Path) -> None:
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    mint_world(bin_dir, tmp_path / "scratch")
    fixture_root = tmp_path / "scratch" / FIXTURE_ENV
    workspace, origins = fixture_root / "workspace", fixture_root / "origins"
    hub_dir = tmp_path / "hub"
    fenced = {**os.environ, "BLIZZARD_MOCK_HARNESS_FENCE": "1"}
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge:
        with run_hub(hub_dir, forge_port, hub_port, test_actions=False) as hub:
            pinned = _pin_each_version(hub, forge)
            config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
            pending_chunk = pinned[_PENDING][1]
            assert forge.post("/_levers/checks_pending", json={"repo": REPO}).status_code == 200
            assert hub.post(f"/api/chunks/{pending_chunk}/promote").status_code == 202
            waiting = _drive_until(config, hub, pending_chunk, fenced, lambda b: b["pending"] is not None)
            assert waiting["open_prs"], waiting

        with _store(hub_dir) as conn:
            polls = conn.execute("SELECT count(*) FROM hub_node_poll WHERE chunk_id = ?", (pending_chunk,)).fetchone()
        assert polls[0] > 0, "the pending chunk recorded no hub_node_poll history"
        _hub_migrate(hub_dir, "--down", _PARENT)
        _held_before_actions(hub_dir, pinned)

        migrated = _hub_migrate(hub_dir)
        assert "store migration warning" not in migrated.stderr, migrated.stderr
        with _store(hub_dir) as conn:
            carried = {
                graph_id: (action, json.loads(args) if args else None, legacy)
                for graph_id, action, args, legacy in conn.execute(
                    "SELECT graph_id, action, action_args, legacy_run FROM graph_nodes WHERE executor = 'hub'"
                )
            }
        for version in _VERSIONS:
            wanted_args = {k: [v] for k, v in version.with_.items()} if version.with_ else None
            assert carried[pinned[version.label][0]] == (version.action, wanted_args, None), version.label

        with run_hub(hub_dir, forge_port, hub_port, test_actions=False, rehost=True) as hub:
            assert forge.delete("/_levers/checks_pending", params={"repo": REPO}).status_code == 200
            details: dict[str, dict[str, Any]] = {}
            for version in _VERSIONS:
                chunk_id = pinned[version.label][1]
                if version.label != _PENDING:
                    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
                status = drive_until_done(config, hub, chunk_id, fenced, timeout=180.0)
                details[version.label] = hub.get(f"/api/chunks/{chunk_id}").json()
                assert status == "done", f"{version.label}: {status!r} {details[version.label]['history']}"

    for label in ("land_pr_ci", "land_default", "land_ff"):
        assert _choices(details[label])[-1] == "landed", (label, details[label]["history"])
        assert details[label]["landed"] is True, label
        assert any(name.startswith("merged/") for name in _artifact_names(details[label])), label
    assert _choices(details[_PENDING]).count("landed") == 1
    assert _choices(details["empty"]) == ["success"]
    assert _choices(details["review_deliver"]) == ["recorded"]
    assert "review-findings-delivered" in _artifact_names(details["review_deliver"])
    assert _choices(details["garden_deliver"]) == ["recorded"]
    assert "garden-delivered" in _artifact_names(details["garden_deliver"])
