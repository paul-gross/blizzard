"""The exported ``events`` dataset, end to end — the `test_egress_events_e2e` scenario of the standing e2e smoke.

One forge, one hub and one runner carry two chunks whose workers read files, invoke a skill and spawn an agent whose
child session reads a file, across both mock harnesses. One chunk's transcript is extended by a resumed session. The hub
derives the events and exports them as NDJSON; the dictionary's ``events_current`` view over the files alone is then
judged against the hub's own ``analytics summary`` counts. A forced re-derivation and a real ``transcript reship`` follow,
which leave the counts where they were. Zero-token, no-network. Skipped unless ``BLIZZARD_E2E=1`` and the fixture
workspace layout is discoverable."""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from blizzard.hub.config import EgressConfig
from blizzard.runner.config import ENV_TRANSCRIPTS_ROOT, RunnerConfig
from blizzard.runner.harness.identity import OPENCODE_HARNESS_ID
from tests.e2e import egress_proof as proof
from tests.e2e.harness_variants import CLAUDE_CODE, OPENCODE_SESSION_MODEL
from tests.e2e.test_acceptance_loop import (
    _PUSH_AND_DECLARE_SCRIPT,
    FIXTURE_ENV,
    REPO,
    REPO_NAME,
    RUNNER_ENV,
    _forge,
    _free_port,
    _hub,
    _mock_bin_dir,
    _runner_api,
    _runner_config,
    _winter_source,
)
from tests.e2e.test_ask_answer_e2e import _tick_until
from tests.egress_recipes import FILES_A_STATION_READ_LAST_WEEK, recipe

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e egress events needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

_RUN = uuid.uuid4().hex[:8]
_TERMINAL = {"done", "stopped", "needs_human"}
_PATH_KEY = f"egress-events-key-{_RUN}"
_PLANTED_INPUT = f"EGRESS-TOOL-INPUT-{_RUN}"
_PLANTED_OUTPUT = f"EGRESS-TOOL-OUTPUT-{_RUN}"
_PLANTED_PROMPT = f"EGRESS-PROMPT-{_RUN}"
_ASK_QUESTION = f"EGRESS-EVENTS-ASK-{_RUN}: which style?"
_REVIEW_SESSION = "events-review"

# What each node reads, relative to the worker's working directory.
_BUILD_READS = ("toy-api/src/main.py", "toy-api/docs/build-notes.md")
_REVIEW_READS = ("toy-api/src/main.py", "toy-api/src/review-target.py")
_CHILD_READS = ("toy-api/src/deep-dive.py",)
_ASK_BEFORE = ("toy-api/docs/ask-before.md",)
_ASK_AFTER = ("toy-api/docs/ask-after.md",)
_PASS = "verdict('pass', 'committed; checks are green')\n"


_PRELUDE = "import os\n_cwd = os.getcwd()\n"


def _read(tool: str, key: str, path: str) -> str:
    return f"tool_call({tool!r}, {{{key!r}: os.path.join(_cwd, {path!r}), 'note': {_PLANTED_INPUT!r}}}, {_PLANTED_OUTPUT!r})\n"


def _commit(filename: str) -> str:
    return (
        "import subprocess, pathlib\n"
        f"repo = {REPO_NAME!r}\n"
        f'(pathlib.Path(repo) / "{filename}").write_text("{filename}\\n")\n'
        'subprocess.run(["git", "-C", repo, "add", "-A"], check=True, capture_output=True)\n'
        "subprocess.run(\n"
        '    ["git", "-C", repo, "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
        f'     "commit", "-m", "{filename}"],\n'
        "    check=True, capture_output=True,\n"
        ")\n" + _PUSH_AND_DECLARE_SCRIPT
    )


def _build_script() -> str:
    reads = "".join(_read("Read", "file_path", path) for path in _BUILD_READS)
    skill = f"tool_call('Skill', {{'skill': 'lint', 'args': {_PLANTED_INPUT!r}}}, {_PLANTED_OUTPUT!r})\n"
    return _PRELUDE + reads + skill + _commit("BUILD.md")


def _review_script() -> str:
    reads = "".join(_read("read", "filePath", path) for path in _REVIEW_READS)
    skill = f"tool_call('skill', {{'name': 'security-review'}}, {_PLANTED_OUTPUT!r})\n"
    child = ", ".join(
        f"{{'tool': 'read', 'input': {{'filePath': os.path.join(_cwd, {path!r})}}, 'output': {_PLANTED_OUTPUT!r}}}"
        for path in _CHILD_READS
    )
    spawn = (
        f"tool_call('task', {{'subagent_type': 'explorer', 'prompt': {_PLANTED_PROMPT!r}, "
        f"'child_tool_calls': [{child}]}}, 'delegated')\n"
    )
    return _PRELUDE + reads + skill + spawn + _commit("REVIEW.md")


def _pass_choice(to: str) -> dict[str, Any]:
    return {"pass": {"description": "Committed and green.", "to": to}}


def _runner_node(prompt: str, to: str, *, session: str | None = None) -> dict[str, Any]:
    node: dict[str, Any] = {
        "executor": "runner",
        "prompt": prompt,
        "judgement": {"prompt": _PASS, "choices": _pass_choice(to)},
        "retries": {"max": 1, "exhausted": "escalate"},
    }
    if session is not None:
        node["session"] = session
    return node


def _deliver(entry: str) -> dict[str, Any]:
    return {
        "executor": "hub",
        "run": [{"command": "python3 -m blizzard.hub.graphs.scripts.land_default"}],
        "judgement": {
            "choices": {
                "landed": {"description": "Landed.", "to": "done"},
                "conflict": {"description": "Conflict; back to the entry.", "to": entry},
            }
        },
    }


def _reads_graph() -> str:
    """``build`` on Claude Code, then ``review`` pinned to OpenCode, then delivery."""
    graph = {
        "name": "default-delivery",
        "entry": "build",
        "sessions": {_REVIEW_SESSION: {"harnesses": [OPENCODE_HARNESS_ID], "model": [OPENCODE_SESSION_MODEL]}},
        "nodes": {
            "build": _runner_node(_build_script(), "review"),
            "review": _runner_node(_review_script(), "deliver", session=f"fresh:{_REVIEW_SESSION}"),
            "deliver": _deliver("build"),
        },
    }
    return CLAUDE_CODE.graph_yaml(graph)


def _ask_graph() -> str:
    before = "".join(_read("Read", "file_path", path) for path in _ASK_BEFORE)
    ask = _PRELUDE + before + f'ask("{_ASK_QUESTION}", ["rest", "graphql"])\n'
    return CLAUDE_CODE.graph_yaml(
        {
            "name": "default-delivery",
            "entry": "clarify",
            "nodes": {"clarify": _runner_node(ask, "deliver"), "deliver": _deliver("clarify")},
        }
    )


def _answer_script() -> str:
    return (
        "import os\n_cwd = os.getcwd()\n"
        + "".join(_read("Read", "file_path", p) for p in _ASK_AFTER)
        + _commit("ASK.md")
    )


@dataclasses.dataclass
class World:
    hub: httpx.Client
    forge: httpx.Client
    hub_url: str
    config: RunnerConfig
    fenced: dict[str, str]
    chunk_ids: list[str] = dataclasses.field(default_factory=list)

    def ingest(self, graph_yaml: str, name: str) -> str:
        assert self.hub.post("/api/graphs", json={"definition_yaml": graph_yaml}).status_code == 201
        issue = self.forge.post(f"/repos/{REPO}/issues", json={"title": f"EGRESS-EVENTS-{_RUN}-{name}", "body": "b"})
        assert issue.status_code == 201, issue.text
        ingested = self.hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert self.hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
        self.chunk_ids.append(chunk_id)
        return chunk_id

    def tick_until(self, chunk_id: str, targets: set[str], timeout: float = 150.0) -> str:
        return _tick_until(self.config, self.hub, chunk_id, self.fenced, targets, timeout)

    def blizzard(self, *args: str, hub: bool = True) -> subprocess.CompletedProcess[str]:
        cli = str(Path(sys.executable).parent / "blizzard")
        command = [cli, *args, *(["--hub-url", self.hub_url] if hub else [])]
        done = subprocess.run(command, capture_output=True, text=True, env=self.fenced)
        assert done.returncode == 0, f"blizzard {' '.join(args)} failed:\n{done.stdout}\n{done.stderr}"
        return done

    def counts(self, dataset: str) -> dict[str, int]:
        rows = json.loads(self.blizzard("hub", "analytics", "summary", dataset, "--json").stdout)["counts"]
        return {row["key"]: row["count"] for row in rows}

    def derive_everything(self) -> None:
        for chunk_id in self.chunk_ids:
            for _ in range(10):
                derived = self.hub.post("/api/analytics/re-derive", json={"chunk_id": chunk_id, "limit": 200})
                assert derived.status_code == 200, derived.text
                if derived.json()["remaining"] == 0:
                    break
            else:
                raise AssertionError(f"derivation of {chunk_id} never converged")


def _wait_for[T](what: str, read: Callable[[], T], done: Callable[[T], bool], timeout: float = 120.0) -> T:
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if done(value):
            return value
        assert time.monotonic() < deadline, f"timed out waiting for {what}; last read: {value!r}"
        time.sleep(1.0)


def _working_directories(config: RunnerConfig) -> list[str]:
    """The directory the export's relative paths are relative to: the one the runner records as the worker's."""
    root = Path(config.workspace_root)
    return sorted({str(root), str(root.resolve())})


def _relative(path: str, working_directories: list[str]) -> str:
    for directory in working_directories:
        if path.startswith(directory + "/"):
            return path[len(directory) + 1 :]
    raise AssertionError(f"{path!r} is under none of {working_directories}")


def _exported(path: str) -> str:
    """A path a script read inside the repository, as the export names it: below the runner's workspace."""
    return f"{RUNNER_ENV}/{path}"


def _hub_counts_agree(world: World, directory: Path) -> list[str]:
    """What differs between the export's ``events_current`` and the hub's own counts; empty when they agree."""
    if not proof.data_files(directory, "ndjson", "events"):
        return ["the export holds no events files yet"]
    connection = proof.events_warehouse(directory)
    cwds = _working_directories(world.config)
    differences: list[str] = []

    def compare(label: str, exported: dict[str, int], hub: dict[str, int]) -> None:
        if exported != hub:
            differences.append(f"{label}: export {exported} != hub {hub}")

    def grouped(sql: str) -> dict[str, int]:
        return dict(connection.execute(sql).fetchall())

    hub_files = {_relative(key, cwds): count for key, count in world.counts("counts-files").items()}
    compare(
        "files",
        grouped("SELECT subject, count(*) FROM events_current WHERE kind = 'file_read' GROUP BY subject"),
        hub_files,
    )
    compare(
        "skills",
        grouped("SELECT subject, count(*) FROM events_current WHERE kind = 'skill_invocation' GROUP BY subject"),
        world.counts("counts-skills"),
    )
    compare(
        "agent types",
        grouped("SELECT agent_type, count(*) FROM events_current WHERE agent_type IS NOT NULL GROUP BY agent_type"),
        world.counts("counts-agent-types"),
    )
    compare(
        "nodes",
        grouped("SELECT node_id, count(*) FROM events_current GROUP BY node_id"),
        world.counts("counts-nodes"),
    )
    return differences


def test_the_exported_events_agree_with_the_hubs_own_counts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A reader holding only the dictionary and the view gets the numbers the hub's analytics report."""
    bin_dir = _mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    winter_source = _winter_source()
    if winter_source is None:
        pytest.skip("no local winter source (set BLIZZARD_MOCK_WINTER_SOURCE)")

    scratch = tmp_path / "scratch"
    subprocess.run(
        [
            str(bin_dir / "blizzard-mock-fixture"),
            "reset",
            "--env",
            FIXTURE_ENV,
            "--scratch-root",
            str(scratch),
            "--winter-source",
            str(winter_source),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    workspace = scratch / FIXTURE_ENV / "workspace"
    origins = scratch / FIXTURE_ENV / "origins"
    (workspace / ".blizzard-mock-harness-fence").write_text("e2e fence marker\n")

    export = tmp_path / "export"
    export.mkdir()
    transcripts_root = tmp_path / "transcripts"
    monkeypatch.setenv(ENV_TRANSCRIPTS_ROOT, str(transcripts_root))
    fenced = dict(os.environ)
    fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
    egress = EgressConfig(directory=export, sweep_seconds=1, settle_seconds=0, min_free_bytes=0)
    forge_port, hub_port = _free_port(), _free_port()

    with (
        _forge(bin_dir, origins, forge_port) as forge,
        _hub(tmp_path / "hub", forge_port, hub_port, egress=egress, extra_env={egress.path_key_env: _PATH_KEY}) as hub,
    ):
        runner_dir = tmp_path / "runner"
        config = dataclasses.replace(
            _runner_config(runner_dir, workspace, bin_dir, hub_port),
            max_agents=1,
            transcripts_root=str(transcripts_root),
            transcripts_ship=True,
        )
        config.config_path.write_text(config.to_toml())
        world = World(hub, forge, f"http://127.0.0.1:{hub_port}", config, fenced)
        with _runner_api(config):
            reads = world.ingest(_reads_graph(), "reads")
            assert world.tick_until(reads, _TERMINAL) == "done"

            ask = world.ingest(_ask_graph(), "ask")
            assert world.tick_until(ask, {"waiting_on_human", *_TERMINAL}) == "waiting_on_human"
            question = hub.get(f"/api/chunks/{ask}").json()["questions"][0]["question_id"]
            world.blizzard("hub", "question", "answer", question, _answer_script(), "--by", "events-resolver")
            assert world.tick_until(ask, _TERMINAL) == "done"

        world.derive_everything()
        cwds = _working_directories(config)

        # 1. The export's current events equal the hub's own counts.
        _wait_for(
            "the export to agree with the hub's counts",
            lambda: _hub_counts_agree(world, export),
            lambda differences: not differences,
        )
        before = proof.events_warehouse(export).execute("SELECT count(*) FROM events_current").fetchone()
        assert before is not None and before[0] > 0

        # 2. The docs' recipe names the files the review station read, the child's included.
        files = proof.events_warehouse(export).execute(recipe(FILES_A_STATION_READ_LAST_WEEK)).fetchall()
        read_by_review = {path for _graph, node, path, _reads in files if node == "review"}
        assert read_by_review == {_exported(p) for p in (*_REVIEW_READS, *_CHILD_READS)}

        # 3. Nothing the transcripts carried but the event itself leaves.
        planted = {
            "tool input": _PLANTED_INPUT,
            "tool output": _PLANTED_OUTPUT,
            "prompt": _PLANTED_PROMPT,
            "path key": _PATH_KEY,
            "ask question": _ASK_QUESTION,
        }
        texts = [
            json.dumps(row, ensure_ascii=False)
            for path in proof.data_files(export, "ndjson", "events")
            for row in proof.decode(path)
        ]
        texts += [p.read_text() for sub in ("_manifests", "_schema") for p in (export / sub).glob("*.json")]
        assert texts
        for kind, value in planted.items():
            assert not any(value in text for text in texts), f"the export carries the planted {kind}"
        assert not any('"payload"' in text for text in texts)
        assert not any(cwd in text for cwd in cwds for text in texts), "a working directory left"

        # 4. A forced re-derivation leaves the counts where they were.
        segments = hub.get(f"/api/chunks/{reads}/transcripts").json()["segments"]
        assert segments, "the reads chunk shipped no transcript"
        segment_id = segments[0]["segment_id"]
        world.blizzard("hub", "analytics", "re-derive", "--segment", segment_id)
        _wait_for(
            "the re-derivation to be exported",
            lambda: (
                proof.events_warehouse(export)
                .execute(
                    "SELECT count(*) FROM events WHERE record_type = 'derivation' AND segment_id = ?", [segment_id]
                )
                .fetchone()
            ),
            lambda row: row is not None and row[0] >= 2,
        )
        assert not _hub_counts_agree(world, export)

        # 5. A reship supersedes the segment: the export drops it, derives its successor, and the counts hold.
        world.blizzard("runner", "transcript", "reship", segment_id, "--dir", str(runner_dir), hub=False)
        _wait_for(
            "the superseded segment to be dropped in the export",
            lambda: (
                proof.events_warehouse(export)
                .execute("SELECT count(*) FROM events WHERE record_type = 'dropped' AND segment_id = ?", [segment_id])
                .fetchone()
            ),
            lambda row: row is not None and row[0] >= 1,
            timeout=240.0,
        )
        world.derive_everything()
        _wait_for(
            "the counts to agree after the reship",
            lambda: _hub_counts_agree(world, export),
            lambda differences: not differences,
        )
