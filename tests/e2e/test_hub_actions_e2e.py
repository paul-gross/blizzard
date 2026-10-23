"""Hub nodes that name an action, end to end through a real hub process: ``land`` lands a chunk against the mock
forge, the two test actions drive hub nodes behind both of their fences, and a hub without the fences refuses them
at mint.

Skipped unless ``BLIZZARD_E2E=1`` with the sibling ``blizzard-mock`` worktree provisioned.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.fleet_launchers import REPO, drive_until_done, git_bare, mock_bin_dir, run_forge, run_hub, runner_config
from tests.fleet_world import FIXTURE_ENV, PUSH_AND_DECLARE_SCRIPT, REPO_NAME, mint_world, scripted_build_prompt
from tests.support import free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e hub actions need the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

_LANDED_FILE = "ACTION_LANDED.md"
_COMMITTING_BUILD = scripted_build_prompt(
    "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    f'(pathlib.Path(repo) / {_LANDED_FILE!r}).write_text("landed by the land action\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: a change the land action lands"],\n'
    "    check=True,\n"
    ")\n" + PUSH_AND_DECLARE_SCRIPT
)
_PASSING_JUDGEMENT = "verdict('pass', 'the mock harness did its turn')\n"


def _graph_yaml(build_prompt: str, hub_nodes: dict[str, dict[str, object]], first_hub_node: str) -> str:
    """A ``build`` runner node passing to ``first_hub_node``, then ``hub_nodes``; named ``default-delivery`` so
    ingest binds it."""
    import yaml

    build = {
        "executor": "runner",
        "prompt": build_prompt,
        "judgement": {
            "prompt": _PASSING_JUDGEMENT,
            "choices": {"pass": {"description": "Built.", "to": first_hub_node}},
        },
        "retries": {"max": 1, "exhausted": "escalate"},
    }
    graph = {"name": "default-delivery", "entry": "build", "nodes": {"build": build, **hub_nodes}}
    return yaml.safe_dump(graph, sort_keys=False)


_LAND_NODES: dict[str, dict[str, object]] = {
    "deliver": {
        "executor": "hub",
        "action": "land",
        "poll_interval": 1,
        "poll_timeout": 600,
        "judgement": {
            "choices": {
                "landed": {"description": "Every repo's PR merged cleanly.", "to": "done"},
                "conflict": {"description": "A repo's PR is dirty; back to build.", "to": "build"},
                "failure": {"description": "A repo could not land; back to build.", "to": "build"},
                "inherited-failure": {"description": "The base check stays red; back to build.", "to": "build"},
            }
        },
    }
}

# `pick` answers `onward` in one pass; `walk` waits one pass, then answers `done`.
_TEST_ACTION_NODES: dict[str, dict[str, object]] = {
    "pick": {
        "executor": "hub",
        "action": "test.answer",
        "with": {"choice": "onward"},
        "judgement": {
            "choices": {
                "onward": {"description": "On to walk.", "to": "walk"},
                "failure": {"description": "The pass failed.", "to": "build"},
            }
        },
    },
    "walk": {
        "executor": "hub",
        "action": "test.sequence",
        "with": {"choices": ["pending", "done"]},
        "poll_interval": 1,
        "poll_timeout": 600,
        "judgement": {
            "choices": {
                "done": {"description": "Walked.", "to": "done"},
                "failure": {"description": "The pass failed.", "to": "build"},
            }
        },
    },
}


def _fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    fixture_root = tmp_path / "scratch" / FIXTURE_ENV
    mint_world(bin_dir, tmp_path / "scratch")
    origins = fixture_root / "origins"
    return bin_dir, fixture_root / "workspace", origins, origins / f"{REPO_NAME}.git"


def _fenced_env() -> dict[str, str]:
    return {**os.environ, "BLIZZARD_MOCK_HARNESS_FENCE": "1"}


def _run_chunk(tmp_path: Path, graph_yaml: str) -> tuple[dict, list[dict], Path, str]:
    """Mint ``graph_yaml``, ingest and promote one issue, drive it to a terminal status, and answer the chunk's
    detail, the forge's pulls, the bare origin, and its ``main`` before the run."""
    bin_dir, workspace, origins, origin_bare = _fixture(tmp_path)
    main_before = git_bare(origin_bare, "rev-parse", "main").strip()
    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        minted = hub.post("/api/graphs", json={"definition_yaml": graph_yaml})
        assert minted.status_code == 201, minted.text
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "actions", "body": "a hub-action chunk"})
        assert issue.status_code == 201, issue.text
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        status = drive_until_done(config, hub, chunk_id, _fenced_env())
        assert status == "done", f"the chunk did not reach done (status {status!r})"
        detail = hub.get(f"/api/chunks/{chunk_id}").json()
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
    return detail, pulls, origin_bare, main_before


def test_land_lands_a_chunk_against_the_mock_forge(tmp_path: Path) -> None:
    """``land`` opens the chunk's PR, merges it, and records the landing: bare ``main`` gains the change exactly
    once, through one merged PR the chunk reports landed."""
    detail, pulls, origin_bare, main_before = _run_chunk(
        tmp_path, _graph_yaml(_COMMITTING_BUILD, _LAND_NODES, "deliver")
    )

    assert detail["landed"] is True
    (landed,) = detail["landed_repos"]
    assert landed["repo"] == REPO
    assert [p["number"] for p in pulls if p.get("merged")] == [p["number"] for p in pulls], pulls
    assert len(pulls) == 1, pulls
    assert detail["open_prs"] == []

    main_after = git_bare(origin_bare, "rev-parse", "main").strip()
    assert main_after != main_before, "bare main did not move"
    assert landed["commit_hash"] == main_after
    landings = [ln for ln in git_bare(origin_bare, "log", "--oneline", "--", _LANDED_FILE).splitlines() if ln.strip()]
    assert len(landings) == 1, f"{_LANDED_FILE} landed {len(landings)} times on bare main"


def test_test_actions_drive_hub_nodes(tmp_path: Path) -> None:
    """``test.answer`` routes its choice in one pass; ``test.sequence`` waits on its first pass and answers
    ``done`` on its second, counting its place by the markers it records."""
    detail, _, _, _ = _run_chunk(tmp_path, _graph_yaml("pass\n", _TEST_ACTION_NODES, "pick"))

    choices = [h["choice_name"] for h in detail["history"]]
    assert choices[-2:] == ["onward", "done"], choices
    sequence = {a["name"]: a.get("content") for a in detail["artifacts"] if a["name"].startswith("test-sequence/")}
    assert sequence == {"test-sequence/0": "pending", "test-sequence/1": "done"}, sequence


def test_a_hub_without_the_test_fences_refuses_the_test_actions(tmp_path: Path) -> None:
    """A hub process started without both fences knows no test action, so a graph naming them is refused at mint,
    each as an unknown action."""
    bin_dir, _, origins, _ = _fixture(tmp_path)
    forge_port, hub_port = free_port(), free_port()
    with (
        run_forge(bin_dir, origins, forge_port),
        run_hub(tmp_path / "hub", forge_port, hub_port, test_actions=False) as hub,
    ):
        refused = hub.post("/api/graphs", json={"definition_yaml": _graph_yaml("pass\n", _TEST_ACTION_NODES, "pick")})
    assert refused.status_code == 422, refused.text
    for action in ("test.answer", "test.sequence"):
        assert f"unknown action `{action}`" in refused.text, refused.text
