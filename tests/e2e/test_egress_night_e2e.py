"""A night of chunks, exported end to end — the `test_egress_night_e2e` scenario of the standing e2e smoke.

One forge, one hub and one runner carry a realistic night: a plain build, a review bounce, a human gate, an ask
answered by a person, a delivery conflict and an escalation, across two graphs that share the ``build`` and ``deliver``
node names. The hub exports it live as NDJSON, is re-hosted over the same record and exports it live again as Parquet,
and a backfill of the night's window — given as UTC instants — writes a third copy. A module fixture runs the night once
and leaves the re-hosted hub up; each test is one proof, reading the directories back the way a warehouse would and
judging them against the hub's own record. Zero-token, no-network. Skipped unless ``BLIZZARD_E2E=1`` and the fixture
workspace layout is discoverable."""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest

from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.runner.config import RunnerConfig
from tests.e2e import egress_proof as proof
from tests.e2e.fleet_traces import PLANT_DIR_VAR, PLANT_LEASE_TOKEN_SCRIPT, planted_lease_tokens
from tests.e2e.harness_variants import CLAUDE_CODE
from tests.e2e.test_acceptance_loop import (
    _PUSH_AND_DECLARE_SCRIPT,
    FIXTURE_ENV,
    REPO,
    REPO_NAME,
    _forge,
    _free_port,
    _hub,
    _mock_bin_dir,
    _runner_api,
    _runner_config,
    _winter_source,
)
from tests.e2e.test_ask_answer_e2e import _tick_until
from tests.runner_join import held_token, token_identity

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e egress night needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

_RUN = uuid.uuid4().hex[:8]
_TERMINAL = {"done", "stopped", "needs_human"}
_DEFAULT_GRAPH = "default-delivery"
# The second graph: it reuses the default graph's `build` and `deliver` node names, so a station is only told apart
# by its graph.
_LANE_GRAPH = f"egress-night-lane-{_RUN}"
# The station built to be the slowest, on the graph whose shape carries it: its worker holds this long, longer than any
# other step of the night.
_SLOW_STATION = "assemble"
_SLOW_GRAPH = _LANE_GRAPH
_SLOW_SECONDS = 15
_RESOLVER = f"egress-night-resolver-{_RUN}"
_ASK_QUESTION = f"EGRESS-ASK-{_RUN}: which API style should the endpoint use?"
_ASK_ANSWER = f"EGRESS-ANSWER-{_RUN}"
_TRANSCRIPT_TOKEN = f"EGRESS-TRANSCRIPT-{_RUN}"
_APPROVE_DESCRIPTION = f"EGRESS-CHOICE-{_RUN} ship it to delivery"
_REJECT_DESCRIPTION = f"EGRESS-CHOICE-{_RUN} send it back to build"
# `egress reset --to` reads a bare local time; the shared window flags are given zoned UTC instants.
_LOCAL_FORMAT = "%Y-%m-%dT%H:%M:%S"
_UTC_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _commit_script(filename: str, message: str, *, before: str = "", after: str = "") -> str:
    """A worker turn: ``before``, a real commit adding ``filename``, then push and declare."""
    return (
        PLANT_LEASE_TOKEN_SCRIPT
        + "import os, subprocess, pathlib, time\n"
        + f"repo = {REPO_NAME!r}\n"
        + f"print({_TRANSCRIPT_TOKEN!r})\n"
        + before
        + f'(pathlib.Path(repo) / "{filename}").write_text("{message}\\n")\n'
        + 'subprocess.run(["git", "-C", repo, "add", "-A"], check=True, capture_output=True)\n'
        + "subprocess.run(\n"
        + '    ["git", "-C", repo, "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
        + f'     "commit", "-m", "{message}"],\n'
        + "    check=True, capture_output=True,\n"
        + ")\n"
        + _PUSH_AND_DECLARE_SCRIPT
        + after
    )


def _deliver(entry: str = "build") -> dict[str, Any]:
    """The hub's landing node; a conflict routes back to the graph's entry."""
    return {
        "executor": "hub",
        "run": [{"command": "python3 -m blizzard.hub.graphs.scripts.land_default"}],
        "judgement": {
            "choices": {
                "landed": {"description": "Landed.", "to": "done"},
                "conflict": {"description": "Conflict; back to the entry.", "to": entry},
                "failure": {"description": "Failed; back to the entry.", "to": entry},
            }
        },
    }


_PASS_JUDGEMENT = "verdict('pass', 'committed; checks are green')\n"
_RETRIES = {"max": 1, "exhausted": "escalate"}


def _build(entry: str, script: str, to: str) -> dict[str, Any]:
    return {
        "executor": "runner",
        "prompt": script,
        "judgement": {
            "prompt": _PASS_JUDGEMENT,
            "choices": {"pass": {"description": "Committed and green.", "to": to}},
        },
        "retries": _RETRIES,
    }


def _graph(nodes: dict[str, Any], entry: str, name: str = _DEFAULT_GRAPH) -> str:
    """A shape's graph, minted just before its chunk is ingested. Ingest pins the enabled ``default-delivery`` graph,
    so a shape of that name is ingested onto itself and a shape of any other name is re-pinned to it by graph id."""
    return CLAUDE_CODE.graph_yaml({"name": name, "entry": entry, "nodes": nodes})


def _plain_graph() -> str:
    script = _commit_script("PLAIN.md", "a plain build", before=f"time.sleep({_SLOW_SECONDS})\n")
    return _graph(
        {_SLOW_STATION: _build(_SLOW_STATION, script, "deliver"), "deliver": _deliver(_SLOW_STATION)},
        _SLOW_STATION,
        _SLOW_GRAPH,
    )


def _bounce_graph() -> str:
    review_judgement = (
        "import pathlib\n"
        "m = pathlib.Path('.review-count')\n"
        "n = (int(m.read_text()) if m.exists() else 0) + 1\n"
        "m.write_text(str(n))\n"
        "if n == 1:\n"
        "    verdict('fail', 'BLOCKING: guard the empty input before delivery')\n"
        "else:\n"
        "    verdict('pass', 'findings addressed; ready to deliver')\n"
    )
    build = _build("build", _commit_script("BOUNCE.md", "a build pass"), "review")
    build["judgement"]["choices"]["fail"] = {"description": "Incomplete.", "to": "build"}
    review = {
        "executor": "runner",
        "prompt": "pass\n",
        "session": "fresh",
        "produces": ["review-findings"],
        "judgement": {
            "prompt": review_judgement,
            "choices": {
                "pass": {"description": "Passes review.", "to": "deliver"},
                "fail": {
                    "description": "Blocking issues found.",
                    "to": "build",
                    "prompt_addendum": _commit_script("ADDRESSED.md", "address review findings"),
                },
            },
        },
        "retries": _RETRIES,
    }
    return _graph({"build": build, "review": review, "deliver": _deliver()}, "build")


def _gate_graph() -> str:
    gate = {
        "executor": "runner",
        "judgement": {
            "by": "human",
            "choices": {
                "approve": {"description": _APPROVE_DESCRIPTION, "to": "deliver"},
                "reject": {"description": _REJECT_DESCRIPTION, "to": "build"},
            },
        },
    }
    build = _build("build", _commit_script("GATED.md", "a change awaiting sign-off"), "approve-gate")
    return _graph({"build": build, "approve-gate": gate, "deliver": _deliver()}, "build")


def _ask_graph() -> str:
    ask = PLANT_LEASE_TOKEN_SCRIPT + f'ask("{_ASK_QUESTION}", ["rest", "graphql"])\n'
    return _graph({"clarify": _build("clarify", ask, "deliver"), "deliver": _deliver("clarify")}, "clarify")


def _conflict_graph() -> str:
    script = _commit_script("CONFLICTED.md", "a change the armed conflict lever rejects")
    return _graph({"build": _build("build", script, "deliver"), "deliver": _deliver()}, "build", _LANE_GRAPH)


def _escalation_graph() -> str:
    # The judgement emits no verdict, so every attempt fails and the retry budget exhausts.
    build = _build("build", "pass\n", "deliver")
    build["judgement"]["prompt"] = "pass\n"
    return _graph({"build": build, "deliver": _deliver()}, "build")


def _answer_script() -> str:
    return _commit_script("ASKED.md", "resolve the ask", before=f"# {_ASK_ANSWER}\n")


@dataclasses.dataclass
class Night:
    hub: httpx.Client
    forge: httpx.Client
    hub_url: str
    config: RunnerConfig
    fenced: dict[str, str]
    chunk_ids: list[str] = dataclasses.field(default_factory=list)
    titles: list[str] = dataclasses.field(default_factory=list)
    bodies: list[str] = dataclasses.field(default_factory=list)

    def ingest(self, graph_yaml: str, name: str) -> str:
        """Mint the shape's graph, file its issue and ingest it onto that graph; the chunk is ready for the runner."""
        minted = self.hub.post("/api/graphs", json={"definition_yaml": graph_yaml})
        assert minted.status_code == 201, minted.text
        graph = minted.json()
        title, body = f"EGRESS-TITLE-{_RUN}-{name}", f"EGRESS-BODY-{_RUN}-{name}"
        issue = self.forge.post(f"/repos/{REPO}/issues", json={"title": title, "body": body})
        assert issue.status_code == 201, issue.text
        ingested = self.hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        if graph["name"] != _DEFAULT_GRAPH:
            pinned = self.hub.patch(f"/api/chunks/{chunk_id}", json={"graph_id": graph["graph_id"]})
            assert pinned.status_code == 202, pinned.text
        assert self.detail(chunk_id)["graph_id"] == graph["graph_id"], f"{name} is not on its own graph"
        assert self.hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
        self.chunk_ids.append(chunk_id)
        self.titles.append(title)
        self.bodies.append(body)
        return chunk_id

    def tick_until(self, chunk_id: str, targets: set[str], timeout: float = 120.0) -> str:
        return _tick_until(self.config, self.hub, chunk_id, self.fenced, targets, timeout)

    def detail(self, chunk_id: str) -> dict[str, Any]:
        return self.hub.get(f"/api/chunks/{chunk_id}").json()

    def blizzard(self, *args: str) -> subprocess.CompletedProcess[str]:
        cli = str(Path(sys.executable).parent / "blizzard")
        done = subprocess.run([cli, *args, "--hub-url", self.hub_url], capture_output=True, text=True)
        assert done.returncode == 0, f"blizzard {' '.join(args)} failed:\n{done.stderr}"
        return done


def _run_the_night(night: Night, planted: dict[str, list[str]]) -> None:
    """The six shapes, one after another, planting what each owns that must never leave."""
    plain = night.ingest(_plain_graph(), "plain")
    assert night.tick_until(plain, _TERMINAL) == "done"

    bounce = night.ingest(_bounce_graph(), "bounce")
    assert night.tick_until(bounce, _TERMINAL) == "done"

    gate = night.ingest(_gate_graph(), "gate")
    assert night.tick_until(gate, {"waiting_on_human", *_TERMINAL}) == "waiting_on_human"
    decision_id = night.detail(gate)["decision"]["decision_id"]
    night.blizzard("hub", "decision", "resolve", decision_id, "approve", "--by", _RESOLVER, "--json")
    assert night.tick_until(gate, _TERMINAL) == "done"
    planted["gate choice description"] = [_APPROVE_DESCRIPTION, _REJECT_DESCRIPTION]
    planted["resolver login"] = [_RESOLVER]

    ask = night.ingest(_ask_graph(), "ask")
    assert night.tick_until(ask, {"waiting_on_human", *_TERMINAL}) == "waiting_on_human"
    question_id = night.detail(ask)["questions"][0]["question_id"]
    night.blizzard("hub", "question", "answer", question_id, _answer_script(), "--by", _RESOLVER)
    assert night.tick_until(ask, _TERMINAL) == "done"
    planted["ask question"] = [_ASK_QUESTION]
    planted["ask answer"] = [_ASK_ANSWER]

    conflict = night.ingest(_conflict_graph(), "conflict")
    armed = night.forge.post("/_levers/merge_conflict", json={"repo": REPO})
    assert armed.status_code == 200, armed.text
    deadline = time.monotonic() + 90
    while not night.detail(conflict)["bounces"] and time.monotonic() < deadline:
        night.tick_until(conflict, _TERMINAL, timeout=2.0)
    assert night.detail(conflict)["bounces"], "the armed conflict never bounced the chunk"
    assert night.forge.delete("/_levers/merge_conflict", params={"repo": REPO}).status_code == 200
    assert night.tick_until(conflict, _TERMINAL) == "done"
    envelope = [a for a in night.detail(conflict)["artifacts"] if a["name"] == "bounce-envelope"]
    assert envelope and envelope[0]["content"], "the bounce left no envelope"
    planted["bounce envelope"] = [line for line in envelope[0]["content"].splitlines() if len(line.strip()) > 12]

    escalation = night.ingest(_escalation_graph(), "escalation")
    assert night.tick_until(escalation, _TERMINAL) == "needs_human"
    planted["takeover command"] = [night.detail(escalation)["escalation"]["takeover_command"]]


def _history_steps(night: Night) -> set[tuple[str, int, str]]:
    """Every step the hub's own record says closed: the chunks' transitions, as (chunk, epoch, node name)."""
    return {
        (chunk_id, h["epoch"], h["from_node_name"])
        for chunk_id in night.chunk_ids
        for h in night.detail(chunk_id)["history"]
        if h["from_node_name"]
    }


def _assert_the_export_holds_the_hub_record(night: Night, rows: dict[str, list[proof.Row]], label: str) -> None:
    """Every closed step of the chunks' history, and every usage fact on the spend surface, is in the export."""
    steps = proof.newest(rows["steps"], "step_key").values()
    held = {(r["chunk_id"], r["epoch"], r["node_name"]) for r in steps}
    assert _history_steps(night) <= held, f"{label}: steps in the hub's history missing from the export"
    invocations = list(proof.newest(rows["invocations"], "usage_id").values())
    spend = {
        row["chunk_id"]: row
        for row in map(
            json.loads,
            night.blizzard("hub", "analytics", "summary", "spend-chunks", "--ndjson").stdout.splitlines(),
        )
    }
    for chunk_id in night.chunk_ids:
        mine = [r for r in invocations if r["chunk_id"] == chunk_id]
        usage = night.detail(chunk_id)["usage"]
        assert len(mine) == len(usage), f"{label}: {chunk_id} has {len(usage)} usage facts, {len(mine)} exported"
        for column in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_create_tokens"):
            assert sum(r[column] for r in mine) == spend[chunk_id][column], f"{label}: {chunk_id} {column}"
        billed = sum((Decimal(r["cost_billed_usd"]) for r in mine if r["cost_billed_usd"]), Decimal(0))
        assert float(billed) == pytest.approx(spend[chunk_id]["cost_usd"]), f"{label}: {chunk_id} billed cost"


def _wait_for[T](what: str, read: Callable[[], T], done: Callable[[T], bool], timeout: float = 90.0) -> T:
    deadline = time.monotonic() + timeout
    while True:
        value = read()
        if done(value):
            return value
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.5)


def _step_sums_equal_its_invocations(rows: dict[str, list[proof.Row]], step_key: str) -> bool:
    step = proof.newest(rows["steps"], "step_key").get(step_key)
    if step is None:
        return False
    mine = [r for r in proof.newest(rows["invocations"], "usage_id").values() if r["step_key"] == step_key]
    if step["invocations"] != len(mine):
        return False
    tokens = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_create_tokens")
    if any(step[c] != sum(r[c] for r in mine) for c in tokens):
        return False
    for column in ("cost_billed_usd", "cost_estimated_usd"):
        amounts = [Decimal(r[column]) for r in mine if r[column] is not None]
        if step[column] != (f"{sum(amounts, Decimal(0)):.9f}" if amounts else None):
            return False
    return True


@dataclasses.dataclass
class Exported:
    """The night, exported: both live directories and their rows, the backfilled copy, what was planted, the late
    usage's step, and the one window the night ran in — UTC instants. ``night.hub`` is the re-hosted Parquet hub,
    up for the whole module."""

    night: Night
    ndjson_dir: Path
    parquet_dir: Path
    ndjson_rows: dict[str, list[proof.Row]]
    parquet_rows: dict[str, list[proof.Row]]
    backfilled: dict[str, list[proof.Row]]
    planted: dict[str, list[str]]
    late_step_key: str
    since: datetime
    until: datetime

    def window(self) -> tuple[str, ...]:
        """The shared ``--since``/``--until`` flags, as zoned instants."""
        return ("--since", self.since.strftime(_UTC_FORMAT), "--until", self.until.strftime(_UTC_FORMAT))


def _forward_late_usage(night: Night, ndjson_dir: Path) -> tuple[str, dict[str, list[proof.Row]]]:
    """A usage fact forwarded on the real wire after the runner's last tick, for a step already exported; returns the
    step's key and the NDJSON rows once the step has absorbed it."""
    plain = night.chunk_ids[0]
    usage = night.detail(plain)["usage"][0]
    step_key = f"{plain}/{usage['epoch']}"
    _wait_for(
        "the plain step in the NDJSON export",
        lambda: proof.read_rows(ndjson_dir, "ndjson"),
        lambda rows: step_key in proof.newest(rows["steps"], "step_key"),
    )
    # Forwarded as the runner itself, under its own token, so the fact lands on its seq line.
    as_runner = night.config.auth_headers()
    seq = night.hub.post("/api/fleet/events", json={"facts": []}, headers=as_runner).json()["high_water"]
    late = {
        "chunk_id": plain,
        "node_id": usage["node_id"],
        "epoch": usage["epoch"],
        "kind": "resume",
        "model": usage["model"],
        "input_tokens": 11,
        "output_tokens": 7,
        "cache_read_tokens": 5,
        "cache_create_tokens": 3,
        "cost_usd": 0.25,
        "estimated_cost_usd": 0.125,
    }
    posted = night.hub.post(
        "/api/fleet/events",
        json={"facts": [{"seq": seq + 1, "kind": "usage.recorded", "payload": late}]},
        headers=as_runner,
    )
    assert posted.status_code == 200 and posted.json()["applied"] == [seq + 1], posted.text
    before_late = len(night.detail(plain)["usage"])
    assert before_late >= 2
    rows = _wait_for(
        "the late usage to reach the NDJSON export",
        lambda: proof.read_rows(ndjson_dir, "ndjson"),
        lambda rows: (
            _step_sums_equal_its_invocations(rows, step_key)
            and proof.newest(rows["steps"], "step_key")[step_key]["invocations"] == before_late
        ),
    )
    assert proof.newest(rows["steps"], "step_key")[step_key]["input_tokens"] >= 11
    return step_key, rows


@pytest.fixture(scope="module")
def exported(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Exported]:
    """Run the night once — live NDJSON, the late usage, the Parquet re-host and reset, the backfill of its window —
    and keep the re-hosted hub up while the module's proofs read it."""
    bin_dir = _mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    winter_source = _winter_source()
    if winter_source is None:
        pytest.skip("no local winter source (set BLIZZARD_MOCK_WINTER_SOURCE)")

    tmp_path = tmp_path_factory.mktemp("night")
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

    ndjson_dir, parquet_dir = tmp_path / "export-ndjson", tmp_path / "export-parquet"
    ndjson_dir.mkdir()
    parquet_dir.mkdir()

    def egress(directory: Path, fmt: str) -> EgressConfig:
        return EgressConfig(directory=directory, format=fmt, sweep_seconds=1, settle_seconds=0, min_free_bytes=0)  # type: ignore[arg-type]

    hub_dir = tmp_path / "hub"
    forge_port, hub_port = _free_port(), _free_port()
    planted: dict[str, list[str]] = {}
    plants = tmp_path / "planted"
    plants.mkdir()
    fenced = dict(os.environ)
    fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
    fenced[PLANT_DIR_VAR] = str(plants)
    since = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=5)
    hub_url = f"http://127.0.0.1:{hub_port}"

    with _forge(bin_dir, origins, forge_port) as forge:
        # -- the night, exported live as NDJSON ---------------------------------------------------------------
        with _hub(hub_dir, forge_port, hub_port, egress=egress(ndjson_dir, "ndjson")) as hub:
            config = dataclasses.replace(
                _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port), max_agents=1
            )
            night = Night(hub, forge, hub_url, config, fenced)
            with _runner_api(config):
                _run_the_night(night, planted)
            planted["work item title"], planted["work item body"] = night.titles, night.bodies
            planted["worker transcript output"] = [_TRANSCRIPT_TOKEN]
            planted["lease token"] = planted_lease_tokens(plants)
            step_key, ndjson_rows = _forward_late_usage(night, ndjson_dir)

        # -- the same record, exported live again as Parquet --------------------------------------------------
        with _hub(hub_dir, forge_port, hub_port, egress=egress(parquet_dir, "parquet"), rehost=True) as hub:
            night.hub = hub
            reset_to = since.astimezone().replace(tzinfo=None).strftime(_LOCAL_FORMAT)
            for dataset in proof.DATASETS:
                night.blizzard("hub", "egress", "reset", "--dataset", dataset, "--to", reset_to)
            parquet_rows = _wait_for(
                "the live Parquet export to hold the night",
                lambda: proof.read_rows(parquet_dir, "parquet"),
                lambda rows: (
                    proof.newest_without_export_time(rows) == proof.newest_without_export_time(ndjson_rows)
                    and _step_sums_equal_its_invocations(rows, step_key)
                ),
            )
            # The window closes on a whole second past every fact of the night, never in the future.
            time.sleep(1.1)
            until = datetime.now(UTC).replace(microsecond=0)
            done = Exported(
                night=night,
                ndjson_dir=ndjson_dir,
                parquet_dir=parquet_dir,
                ndjson_rows=proof.read_rows(ndjson_dir, "ndjson"),
                parquet_rows=parquet_rows,
                backfilled={},
                planted=planted,
                late_step_key=step_key,
                since=since,
                until=until,
            )
            night.blizzard("hub", "egress", "backfill", *done.window())
            done.backfilled = proof.read_rows(parquet_dir, "parquet", backfill=True)
            yield done


def test_both_live_exports_hold_the_same_newest_rows(exported: Exported) -> None:
    """The NDJSON and Parquet live exports hold the same newest copies, excluding ``exported_at``, and none is empty."""
    ndjson_rows, parquet_rows = exported.ndjson_rows, exported.parquet_rows
    assert proof.newest_without_export_time(parquet_rows) == proof.newest_without_export_time(ndjson_rows)
    assert all(proof.newest(ndjson_rows[d], i) for d, i in proof.DATASETS.items())


def test_both_exports_hold_the_hubs_record(exported: Exported) -> None:
    """Every step of the chunks' history and every usage fact on the spend surface is in both exports."""
    _assert_the_export_holds_the_hub_record(exported.night, exported.ndjson_rows, "ndjson")
    _assert_the_export_holds_the_hub_record(exported.night, exported.parquet_rows, "parquet")


def test_both_exports_name_the_runner_by_its_minted_id_and_its_name(exported: Exported) -> None:
    """Every step the runner ran and every invocation carries the id the hub minted for the runner, beside
    the name it registered under."""
    night = exported.night
    joined = token_identity(night.hub_url, held_token(night.config.root))
    assert joined.status_code == 200, joined.text
    runner = (joined.json()["runner_id"], night.config.name)
    assert runner[0].startswith("rn_"), runner
    for label, rows in (("ndjson", exported.ndjson_rows), ("parquet", exported.parquet_rows)):
        steps = proof.newest(rows["steps"], "step_key").values()
        ran = {(r["runner_id"], r["runner_name"]) for r in steps if r["runner_id"] is not None}
        assert ran == {runner}, f"{label} steps: {ran}"
        invocations = proof.newest(rows["invocations"], "usage_id").values()
        billed = {(r["runner_id"], r["runner_name"]) for r in invocations}
        assert billed == {runner}, f"{label} invocations: {billed}"


def test_manifests_name_every_file(exported: Exported) -> None:
    """Every data file, live and backfilled, is named by a manifest with its row count and SHA-256."""
    proof.assert_manifests_name_every_file(exported.ndjson_dir, "ndjson")
    proof.assert_manifests_name_every_file(exported.parquet_dir, "parquet")


def test_nothing_planted_leaves(exported: Exported) -> None:
    """What never leaves, did not: nothing planted is in any decoded row, manifest or schema."""
    assert set(exported.planted) >= {
        "work item title",
        "work item body",
        "ask question",
        "ask answer",
        "gate choice description",
        "resolver login",
        "bounce envelope",
        "worker transcript output",
        "takeover command",
        "lease token",
    }
    proof.assert_nothing_planted(exported.ndjson_dir, "ndjson", exported.planted)
    proof.assert_nothing_planted(exported.parquet_dir, "parquet", exported.planted)


def test_late_usage_converged_in_both(exported: Exported) -> None:
    """A usage fact forwarded after its step was exported converged the step to the sum of its invocations."""
    assert _step_sums_equal_its_invocations(exported.ndjson_rows, exported.late_step_key)
    assert _step_sums_equal_its_invocations(exported.parquet_rows, exported.late_step_key)


def test_a_backfill_of_the_window_equals_the_live_export(exported: Exported) -> None:
    """A backfill of the night's window, given as zoned instants, writes the rows the live exports hold."""
    assert any(exported.backfilled.values()), "the backfill wrote nothing"
    live = proof.newest_without_export_time(exported.parquet_rows)
    assert proof.newest_without_export_time(exported.backfilled) == live


def test_recipes_agree_with_the_hubs_analytics(exported: Exported) -> None:
    """The docs' cost recipe matches ``spend-nodes`` over the night's window per station, and the slowest-station
    recipe names the station built to be slowest, on its own graph."""
    spend = json.loads(
        exported.night.blizzard("hub", "analytics", "summary", "spend-nodes", *exported.window(), "--json").stdout
    )
    node_station = {
        r["node_id"]: (r["graph_name"], r["node_name"])
        for r in proof.newest(exported.parquet_rows["steps"], "step_key").values()
    }
    hub_cost: dict[proof.Station, list[float]] = {}
    for row in spend["spend"]:
        billed, estimated = hub_cost.setdefault(node_station[row["key"]], [0.0, 0.0])
        hub_cost[node_station[row["key"]]] = [
            billed + (row["cost_usd"] or 0.0),
            estimated + (row["estimated_cost_usd"] or 0.0),
        ]
    for fmt, directory in (("ndjson", exported.ndjson_dir), ("parquet", exported.parquet_dir)):
        recipe_cost = proof.cost_by_station(directory, fmt)
        # A station with no usage (a gate, the hub's own steps) has a cost row but no spend row.
        assert set(hub_cost) <= set(recipe_cost), fmt
        for station, (billed, estimated) in recipe_cost.items():
            expected_billed, expected_estimated = hub_cost.get(station, (0.0, 0.0))
            assert float(billed) == pytest.approx(expected_billed), (fmt, station)
            assert float(estimated) == pytest.approx(expected_estimated), (fmt, station)
        assert proof.slowest_station(directory, fmt) == (_SLOW_GRAPH, _SLOW_STATION), fmt


def test_cost_by_node_splits_a_shared_node_name_by_graph(exported: Exported) -> None:
    """The cost recipe keeps ``build`` and ``deliver`` on each of the two graphs that name them as distinct
    stations, never one merged row per node name."""
    for fmt, directory in (("ndjson", exported.ndjson_dir), ("parquet", exported.parquet_dir)):
        stations = set(proof.cost_by_station(directory, fmt))
        for node in ("build", "deliver"):
            graphs = {graph for graph, name in stations if name == node}
            assert graphs == {_DEFAULT_GRAPH, _LANE_GRAPH}, (fmt, node, graphs)


def test_published_newest_views_return_the_newest_copies(exported: Exported) -> None:
    """The published newest-copy views return the identities the rows' own newest copies name."""
    for directory, fmt in ((exported.ndjson_dir, "ndjson"), (exported.parquet_dir, "parquet")):
        rows = proof.read_rows(directory, fmt)
        views = proof.published_newest_identities(directory, fmt)
        assert views == {d: set(proof.newest(rows[d], i)) for d, i in proof.DATASETS.items()}, fmt
