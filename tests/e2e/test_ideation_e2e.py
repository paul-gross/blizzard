"""The packaged ``ideation`` graph end to end — the `test_ideation_e2e` scenario of the
standing e2e smoke: ``test_garden_routine_e2e.py``'s shape for the survey -> reconcile ->
propose -> deliver loop, ``test_ask_answer_e2e.py``'s for the ask-parks-then-answer
mechanics the undeclared-axis path and the delivery-bounce escalation depend on. The real
packaged YAML is minted with its prompts swapped for scripts and its deliver command wrapped
in a crash-once shim; every delta carries ``findings: []``, and an undeclared axis ends in `ask`."""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.graphs import PACKAGED
from tests.e2e.test_acceptance_loop import (
    FIXTURE_ENV,
    _drive_until_done,
    _forge,
    _free_port,
    _hub,
    _mock_bin_dir,
    _runner_config,
    _winter_source,
)
from tests.e2e.test_ask_answer_e2e import _runner_api, _tick_until
from tests.e2e.test_session_modes_e2e import _sessions_by_node

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e ideation needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

_ROUTINE = "ideation-e2e"

# The scripted prompts branch on the run's `--note` (`path=<value>` in the work item body).
_SCOPE_NOVEL = "ideation-novel"
_SCOPE_CLEAN = "ideation-clean"
_SCOPE_NONE = "ideation-none"
_SCOPE_INVALID = "ideation-invalid"
_SCOPE_FAILURE = "ideation-failure"
_SCOPE_ESCALATE = "ideation-escalate"
_SCOPE_UNDECLARED = "ideation-undeclared"
_SCOPE_DECLARED_LATER = "ideation-declared-later"


def _common(crash_dir: Path) -> str:
    """The shared preamble: the charge parsed off the real work item and the worker
    artifact verbs — no findings bucket, since this graph has none. `staged()` reads this
    node-step's own not-yet-published submissions (`artifact staged --content`): a
    judgement turn reads back what its base prompt just created within the same in-flight
    attempt, which `artifact get` (last *published* epoch only) 404s on."""
    return (
        "import json, os, pathlib, subprocess\n"
        "chunk_id = os.environ['BLIZZARD_CHUNK_ID']\n"
        "def sh(*args, inp=None):\n"
        "    return subprocess.run(list(args), input=inp, check=True, capture_output=True, text=True).stdout\n"
        "item = json.loads(sh('blizzard', 'runner', 'work-items', chunk_id))['items'][0]\n"
        "lines = item['body'].splitlines()\n"
        "scope = next(l for l in lines if l.startswith('Scope:')).split()[1]\n"
        "path = next(l.split('=', 1)[1] for l in lines if l.startswith('path='))\n"
        "def publish(name, content):\n"
        "    sh('blizzard', 'runner', 'artifact', 'create', '--name', name, inp=content)\n"
        "def staged(name):\n"
        "    entries = json.loads(sh('blizzard', 'runner', 'artifact', 'staged', '--content'))\n"
        "    return next(e['content'] for e in entries if e['name'] == name)\n"
        "axis_marker = pathlib.Path(f'.ideation-axis-{chunk_id}')\n"
        "armed_marker = pathlib.Path(f'.ideation-crash-armed-{chunk_id}')\n"
        f"crash_trigger = pathlib.Path({str(crash_dir)!r}) / chunk_id\n"
    )


def _crash_once_command(crash_dir: Path) -> str:
    """The deliver command behind a shim that exits 1 once, iff propose left this chunk's
    trigger — so the real ``garden_deliver`` runs on every other visit unchanged."""
    return (
        f'sh -c \'m={str(crash_dir)!r}/$BZ_HUB_CHUNK_ID; if [ -e "$m" ]; then rm -f "$m"; '
        'echo "simulated delivery crash" >&2; exit 1; fi; '
        "exec python3 -m blizzard.hub.graphs.scripts.garden_deliver --delta delta --proposals docket'"
    )


# survey: read the format from system scope, ask on the ask-driven paths (ending the turn), else sweep.
_SURVEY = (
    "fmt = sh('blizzard', 'runner', 'artifact', 'get', '--scope', 'system', 'garden/finding-format', '--content')\n"
    "assert 'FindingDelta' in fmt, 'system-scope finding format did not resolve'\n"
    "if path in ('ask-undeclared', 'ask-declared'):\n"
    "    question = (\n"
    "        f'no gardening-axes registry entry declares the ideation-e2e axis for {scope} — '\n"
    "        'declare it or confirm its absence?'\n"
    "    )\n"
    "    ask(question, ['declared', 'still-undeclared'])\n"
    "if path in ('novel', 'invalid', 'escalate', 'failure', 'reconcile-drop'):\n"
    "    cands = [\n"
    "        {'ref': 'C1', 'class': 'missing-capability', 'locus': f'{scope}: persona-a direction gap',\n"
    "         'summary': 'persona A cannot do the core workflow at all'},\n"
    "        {'ref': 'C2', 'class': 'partial-capability', 'locus': f'{scope}: persona-b tweak gap',\n"
    "         'summary': 'persona B only gets this on one surface'},\n"
    "        {'ref': 'C3', 'class': 'unwanted-capability', 'locus': f'{scope}: persona-c retire gap',\n"
    "         'summary': 'a surface the charter no longer asks for'},\n"
    "    ]\n"
    "elif path == 'revive':\n"
    "    cands = [{'ref': 'C2', 'class': 'partial-capability', 'locus': f'{scope}: persona-b tweak gap',\n"
    "              'summary': 'changed since the pass: the parity gap is no longer intentional'}]\n"
    "elif path == 'none':\n"
    "    cands = [{'ref': 'C1', 'class': 'minor-gap', 'locus': f'{scope}: persona-d minor gap',\n"
    "              'summary': 'a minor gap not worth a proposal'}]\n"
    "else:\n"
    "    cands = []\n"
    "measurement = f'e2e ideation sweep of {scope}: {len(cands)} flagged, 0 proposed'\n"
    "publish('survey', json.dumps({'scope': scope, 'revisions': {}, 'measurement': measurement, 'candidates': cands}))\n"
    "publish('delta', json.dumps({'scope': scope, 'revisions': {}, 'measurement': measurement, 'findings': []}))\n"
)

_SURVEY_JUDGEMENT = (
    "if axis_marker.exists():\n"
    "    choice = 'undeclared' if axis_marker.read_text().strip() == 'undeclared' else 'empty'\n"
    "else:\n"
    "    choice = {\n"
    "        'novel': 'found', 'invalid': 'found', 'escalate': 'found', 'failure': 'found',\n"
    "        'reconcile-drop': 'found', 'revive': 'found', 'none': 'found', 'clean': 'empty',\n"
    "    }[path]\n"
    "verdict(choice, 'scripted sweep')\n"
)


def _answer_axis_script(marker_value: str, measurement_tail: str) -> str:
    """The answer text delivered via `blizzard hub question answer` for the undeclared-
    axis ask — a self-contained script, never a continuation of survey's own base prompt
    (`test_ask_answer_e2e.py`'s `_ANSWER_SCRIPT` shape): it decides from the "human"'s
    word whether the axis is now declared, publishes the always-empty survey/delta either
    way, and leaves a marker survey's own judgement script reads to pick its edge."""
    return (
        "import json, os, pathlib, subprocess\n"
        "chunk_id = os.environ['BLIZZARD_CHUNK_ID']\n"
        "def sh(*args, inp=None):\n"
        "    return subprocess.run(list(args), input=inp, check=True, capture_output=True, text=True).stdout\n"
        "def publish(name, content):\n"
        "    sh('blizzard', 'runner', 'artifact', 'create', '--name', name, inp=content)\n"
        "item = json.loads(sh('blizzard', 'runner', 'work-items', chunk_id))['items'][0]\n"
        "scope = next(l for l in item['body'].splitlines() if l.startswith('Scope:')).split()[1]\n"
        f"measurement = f'e2e ideation sweep of {{scope}}: {measurement_tail}'\n"
        "publish('survey', json.dumps({'scope': scope, 'revisions': {}, 'measurement': measurement, 'candidates': []}))\n"
        "publish('delta', json.dumps({'scope': scope, 'revisions': {}, 'measurement': measurement, 'findings': []}))\n"
        f"pathlib.Path(f'.ideation-axis-{{chunk_id}}').write_text({marker_value!r})\n"
    )


_ANSWER_STILL_UNDECLARED = _answer_axis_script("undeclared", "axis still undeclared, nothing to sweep")
_ANSWER_NOW_DECLARED = _answer_axis_script("declared", "0 flagged after the registry was updated")
_ASK_AXIS_SUBSTR = "ideation-e2e axis"

# reconcile: open/accepted drop; a pass drops unless the candidate says what changed since its reason.
_RECONCILE = (
    "survey_asset = json.loads(sh('blizzard', 'runner', 'artifact', 'get', 'survey', '--content'))\n"
    "proposals = json.loads(sh('blizzard', 'runner', 'garden', 'proposals', '--state', 'all'))\n"
    "def weigh(cand):\n"
    "    for p in proposals:\n"
    "        if cand['locus'] not in p['title']:\n"
    "            continue\n"
    "        closure = p.get('closure')\n"
    "        if closure is None or closure['closure'] == 'accepted':\n"
    "            return 'drop', None\n"
    "        assert closure['closure'] == 'passed', closure\n"
    "        assert closure['reason'], f'passed proposal {p[\"proposal_id\"]} carried no reason to weigh'\n"
    "        if cand['summary'].startswith('changed since the pass'):\n"
    "            return 'revive', p\n"
    "        return 'drop', None\n"
    "    return 'keep', None\n"
    "shortlist = []\n"
    "for cand in survey_asset['candidates']:\n"
    "    outcome, earlier = weigh(cand)\n"
    "    if outcome == 'drop':\n"
    "        continue\n"
    "    entry = dict(cand)\n"
    "    if outcome == 'revive':\n"
    "        entry['revives'] = earlier['proposal_id']\n"
    "        entry['changed'] = cand['summary']\n"
    "    shortlist.append(entry)\n"
    "publish('shortlist', json.dumps(shortlist))\n"
)

_RECONCILE_JUDGEMENT = (
    "shortlist = json.loads(staged('shortlist'))\nverdict('novel' if shortlist else 'nothing-new', 'scripted match')\n"
)

# propose: one proposal per entry, `findings` always `[]`; the bounce paths mis-scope the delta on purpose.
_PROPOSE = (
    "fmt = sh('blizzard', 'runner', 'artifact', 'get', '--scope', 'system', 'garden/proposal-format', '--content')\n"
    "assert 'GardenProposalCandidate' in fmt, 'system-scope proposal format did not resolve'\n"
    "classes_text = sh('blizzard', 'runner', 'artifact', 'get', 'classes', '--scope', 'graph', '--content')\n"
    "assert 'direction' in classes_text, 'graph-scope classes artifact did not resolve'\n"
    "shortlist = json.loads(sh('blizzard', 'runner', 'artifact', 'get', 'shortlist', '--content'))\n"
    "class_by_ref = {'C1': 'direction', 'C2': 'tweak', 'C3': 'retire'}\n"
    "if path == 'none':\n"
    "    docket = []\n"
    "else:\n"
    "    docket = [\n"
    "        {'ref': f\"P-{c['ref']}\", 'class': class_by_ref.get(c['ref'], 'tweak'),\n"
    "         'title': f\"ideation proposal: {c['locus']}\",\n"
    "         'body': (f\"revives {c['revives']} — {c['changed']}\" if 'revives' in c\n"
    "                  else f\"scripted proposal citing no findings, addressing: {c['summary']}\"),\n"
    "         'findings': []}\n"
    "        for c in shortlist\n"
    "    ]\n"
    "if path == 'failure' and not armed_marker.exists():\n"
    "    crash_trigger.write_text('crash once')\n"
    "    armed_marker.write_text('armed')\n"
    "measurement = f'e2e ideation sweep of {scope}: {len(docket)} proposed'\n"
    "delta_scope = ('not-' + scope) if path in ('invalid', 'escalate') else scope\n"
    "publish('docket', json.dumps(docket))\n"
    "publish('delta', json.dumps({'scope': delta_scope, 'revisions': {}, 'measurement': measurement, 'findings': []}))\n"
)

_PROPOSE_JUDGEMENT = (
    "docket = json.loads(staged('docket'))\nverdict('proposed' if docket else 'none', 'scripted propose')\n"
)

# The `invalid` addendum, appended to propose's re-entry; a SECOND bounce asks instead of resubmitting.
_PROPOSE_FROM_DELIVER_INVALID = (
    "failure = sh('blizzard', 'runner', 'artifact', 'get', 'garden-delivery-failure', '--content')\n"
    "assert 'scope' in failure, f'failure artifact does not name the rejected scope: {failure!r}'\n"
    "history = json.loads(sh('blizzard', 'runner', 'chunk', 'history'))\n"
    "invalid_count = sum(\n"
    "    1 for r in history\n"
    "    if r.get('kind') == 'transition' and r.get('from_node') == 'deliver' and r.get('choice') == 'invalid'\n"
    ")\n"
    "if invalid_count >= 2:\n"
    "    ask(f'ideation delivery for {scope} bounced invalid twice — resolve the delta shape by hand?',\n"
    "        ['retry', 'abandon'])\n"
    "else:\n"
    "    corrected_scope = ('not-' + scope) if path == 'escalate' else scope\n"
    "    publish('docket', json.dumps(docket))\n"
    "    publish('delta', json.dumps({'scope': corrected_scope, 'revisions': {}, 'measurement': measurement,\n"
    "                                  'findings': []}))\n"
)

# The `failure` addendum, appended to propose's re-entry: confirm, republish unchanged, bound the loop.
_PROPOSE_FROM_DELIVER_FAILURE = (
    "history = json.loads(sh('blizzard', 'runner', 'chunk', 'history'))\n"
    "failure_count = sum(\n"
    "    1 for r in history\n"
    "    if r.get('kind') == 'transition' and r.get('from_node') == 'deliver' and r.get('choice') == 'failure'\n"
    ")\n"
    "assert failure_count >= 1, f'the failure addendum ran with no failure transition on record: {history}'\n"
    "if failure_count >= 2:\n"
    "    ask(f'ideation delivery for {scope} failed twice — repair the delivery path?', ['retry', 'abandon'])\n"
    "assert json.loads(staged('docket')) == docket, 'the docket changed across the failure bounce'\n"
    "publish('docket', json.dumps(docket))\n"
    "publish('delta', json.dumps({'scope': scope, 'revisions': {}, 'measurement': measurement, 'findings': []}))\n"
)

# The answer to the escalation ask: a self-contained script that fixes the delta's scope.
_ANSWER_ESCALATE_RESOLVED = (
    "import json, os, subprocess\n"
    "chunk_id = os.environ['BLIZZARD_CHUNK_ID']\n"
    "def sh(*args, inp=None):\n"
    "    return subprocess.run(list(args), input=inp, check=True, capture_output=True, text=True).stdout\n"
    "def publish(name, content):\n"
    "    sh('blizzard', 'runner', 'artifact', 'create', '--name', name, inp=content)\n"
    "item = json.loads(sh('blizzard', 'runner', 'work-items', chunk_id))['items'][0]\n"
    "scope = next(l for l in item['body'].splitlines() if l.startswith('Scope:')).split()[1]\n"
    # `docket` was staged by this same still-in-flight node-step's bounced turn — not yet
    # published, so it comes back via `artifact staged`, never `artifact get`.
    "staged_entries = json.loads(sh('blizzard', 'runner', 'artifact', 'staged', '--content'))\n"
    "docket = json.loads(next(e['content'] for e in staged_entries if e['name'] == 'docket'))\n"
    "measurement = f'e2e ideation sweep of {scope}: {len(docket)} proposed'\n"
    "publish('docket', json.dumps(docket))\n"
    "publish('delta', json.dumps({'scope': scope, 'revisions': {}, 'measurement': measurement, 'findings': []}))\n"
)
_ESCALATE_QUESTION_SUBSTR = "bounced invalid twice"


def _scripted_ideation_graph_yaml(crash_dir: Path) -> str:
    """The real packaged ``ideation`` body with its prompts swapped for scripts and its
    deliver command behind the crash-once shim — nodes, edges, session pools, and the
    `classes` graph-scoped artifact stay verbatim."""
    body = PACKAGED.named("ideation").body
    common = _common(crash_dir)
    nodes: dict[str, object] = body["nodes"]  # type: ignore[assignment]
    nodes["survey"]["prompt"] = common + _SURVEY  # type: ignore[index]
    nodes["survey"]["judgement"]["prompt"] = common + _SURVEY_JUDGEMENT  # type: ignore[index]
    nodes["reconcile"]["prompt"] = common + _RECONCILE  # type: ignore[index]
    nodes["reconcile"]["judgement"]["prompt"] = common + _RECONCILE_JUDGEMENT  # type: ignore[index]
    nodes["propose"]["prompt"] = common + _PROPOSE  # type: ignore[index]
    nodes["propose"]["judgement"]["prompt"] = common + _PROPOSE_JUDGEMENT  # type: ignore[index]
    nodes["deliver"]["judgement"]["choices"]["invalid"]["prompt_addendum"] = _PROPOSE_FROM_DELIVER_INVALID  # type: ignore[index]
    nodes["deliver"]["judgement"]["choices"]["failure"]["prompt_addendum"] = _PROPOSE_FROM_DELIVER_FAILURE  # type: ignore[index]
    nodes["deliver"]["run"][0]["command"] = _crash_once_command(crash_dir)  # type: ignore[index]
    return yaml.safe_dump(body, sort_keys=False)


def _edges(hub, chunk_id: str) -> list[tuple[str | None, str | None]]:
    """The chunk's transition history as ``(from_node_name, choice_name)`` pairs."""
    detail = hub.get(f"/api/chunks/{chunk_id}")
    assert detail.status_code == 200, detail.text
    return [(t["from_node_name"], t["choice_name"]) for t in detail.json()["history"]]


def test_ideation_runs_end_to_end_on_all_authored_paths(tmp_path: Path) -> None:
    """One routine, several runs over the packaged `ideation` graph: three classes proposed
    and closed by a person, a drop and a revive off the pass reason, an empty sweep, a
    decline, an invalid bounce, a crash bounce, a double bounce that asks, both ask branches."""
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
    (workspace / ".blizzard-mock-harness-fence").write_text("e2e fence marker\n")
    crash_dir = tmp_path / "crash-once"
    crash_dir.mkdir()

    forge_port, hub_port = _free_port(), _free_port()
    runner_dir = tmp_path / "runner"
    with (
        _forge(bin_dir, scratch / FIXTURE_ENV / "origins", forge_port),
        _hub(tmp_path / "hub", forge_port, hub_port) as hub,
    ):
        minted = hub.post("/api/graphs", json={"definition_yaml": _scripted_ideation_graph_yaml(crash_dir)})
        assert minted.status_code == 201, minted.text

        created = hub.post(
            "/api/routines",
            json={
                "name": _ROUTINE,
                "graph_name": "ideation",
                "default_scope_slug": _SCOPE_NOVEL,
                "default_model": [],
                "default_effort": None,
            },
        )
        assert created.status_code == 201, created.text
        routine_id = created.json()["routine_id"]

        for slug in (
            _SCOPE_CLEAN,
            _SCOPE_NONE,
            _SCOPE_INVALID,
            _SCOPE_FAILURE,
            _SCOPE_ESCALATE,
            _SCOPE_UNDECLARED,
            _SCOPE_DECLARED_LATER,
        ):
            scope_created = hub.post("/api/scopes", json={"slug": slug, "description": ""})
            assert scope_created.status_code == 201, scope_created.text
            scope_linked = hub.put(f"/api/routines/{routine_id}/scopes/{slug}")
            assert scope_linked.status_code == 204, scope_linked.text

        config = dataclasses.replace(_runner_config(runner_dir, workspace, bin_dir, hub_port), max_agents=1)
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"

        def run(path: str, scope: str, *, timeout: float = 180.0) -> str:
            minted_run = hub.post(
                f"/api/routines/{routine_id}/run",
                json={"scope_slug": scope, "mode": "full", "note": f"path={path}"},
            )
            assert minted_run.status_code == 201, minted_run.text
            chunk_id = minted_run.json()["chunk_id"]
            status = _drive_until_done(config, hub, chunk_id, fenced, timeout=timeout)
            assert status == "done", f"{path} run did not reach done (last status {status!r}): {_edges(hub, chunk_id)}"
            return chunk_id

        def run_via_ask(
            path: str, scope: str, answer_script: str, *, question_substr: str, timeout: float = 180.0
        ) -> str:
            """Mint a run expected to park on an `ask`, answer it, and drive to done — the
            `test_ask_answer_e2e.py` phases, reused for both survey's undeclared-axis ask
            and propose's delivery-escalation ask (never `_drive_until_done`, which opens
            its own `_runner_api` and cannot pause mid-drive to answer)."""
            minted_run = hub.post(
                f"/api/routines/{routine_id}/run",
                json={"scope_slug": scope, "mode": "full", "note": f"path={path}"},
            )
            assert minted_run.status_code == 201, minted_run.text
            chunk_id = minted_run.json()["chunk_id"]
            with _runner_api(config):
                status = _tick_until(
                    config, hub, chunk_id, fenced, {"waiting_on_human", "done", "needs_human"}, timeout
                )
                assert status == "waiting_on_human", f"{path} chunk did not park (last status {status!r})"
                detail = hub.get(f"/api/chunks/{chunk_id}").json()
                assert detail["questions"], "the parked chunk should surface its open question"
                question = detail["questions"][0]
                assert question_substr in question["question"], question
                answered = subprocess.run(
                    [
                        str(Path(sys.executable).parent / "blizzard"),
                        "hub",
                        "question",
                        "answer",
                        question["question_id"],
                        answer_script,
                        "--by",
                        "alice",
                        "--hub-url",
                        f"http://127.0.0.1:{hub_port}",
                    ],
                    capture_output=True,
                    text=True,
                )
                assert answered.returncode == 0, f"hub question answer failed:\n{answered.stderr}"
                status = _tick_until(config, hub, chunk_id, fenced, {"done", "needs_human", "stopped"}, timeout)
                assert status == "done", f"{path} chunk did not reach done after the answer (last status {status!r})"
            return chunk_id

        # -- novel: survey -> reconcile -> propose (all three classes) -> deliver -------
        found_chunk = run("novel", _SCOPE_NOVEL)
        assert _edges(hub, found_chunk) == [
            ("survey", "found"),
            ("reconcile", "novel"),
            ("propose", "proposed"),
            ("deliver", "recorded"),
        ]

        delta_resp = hub.get(f"/api/runs/{found_chunk}")
        assert delta_resp.status_code == 200, delta_resp.text
        delta = delta_resp.json()
        assert delta["outcome"] == ChunkStatus.DONE
        (set_delta,) = delta["sets"]
        assert set_delta["added"] == [], set_delta
        assert set_delta["observed"] == [], set_delta
        assert set_delta["gone"] == [], set_delta
        assert set_delta["revisions"] == {}
        assert set_delta["measurement"] == f"e2e ideation sweep of {_SCOPE_NOVEL}: 3 proposed"

        all_proposals = hub.get("/api/garden-proposals").json()["proposals"]
        novel_proposals = [p for p in all_proposals if _SCOPE_NOVEL in p["title"]]
        assert len(novel_proposals) == 3, novel_proposals
        assert {p["class"] for p in novel_proposals} == {"direction", "tweak", "retire"}
        assert all(p["findings"] == [] for p in novel_proposals), novel_proposals
        assert all(p["routine_name"] == _ROUTINE for p in novel_proposals)

        direction_proposal = next(p for p in novel_proposals if p["class"] == "direction")
        tweak_proposal = next(p for p in novel_proposals if p["class"] == "tweak")

        # Accept one (no work item), pass another with a reason, leave the third open:
        # all three states drop the candidate on the routine's next run.
        accepted = hub.post(
            f"/api/garden-proposals/{direction_proposal['proposal_id']}/accept",
            json={"mint_work_item": False, "reason": "clear win, no ticket needed yet"},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["chunk_id"] is None
        assert accepted.json()["closure"]["closure"] == "accepted"

        passed = hub.post(
            f"/api/garden-proposals/{tweak_proposal['proposal_id']}/pass",
            json={"reason": "not now — the surface parity gap is intentional this quarter"},
        )
        assert passed.status_code == 200, passed.text
        assert passed.json()["closure"]["closure"] == "passed"

        # -- reconcile-drop: a second run over the same scope finds every candidate
        #    already answered — straight to deliver, zero new proposals -----------------
        drop_chunk = run("reconcile-drop", _SCOPE_NOVEL)
        assert _edges(hub, drop_chunk) == [("survey", "found"), ("reconcile", "nothing-new"), ("deliver", "recorded")]
        after_proposals = hub.get("/api/garden-proposals").json()["proposals"]
        assert len([p for p in after_proposals if p["routine_name"] == _ROUTINE]) == 3, after_proposals
        (drop_set,) = hub.get(f"/api/runs/{drop_chunk}").json()["sets"]
        assert drop_set["measurement"] == f"e2e ideation sweep of {_SCOPE_NOVEL}: 3 flagged, 0 proposed"

        # -- revive: the same passed candidate returns saying what changed since the pass
        #    reason; reconcile keeps it and propose names the earlier proposal ------------
        revive_chunk = run("revive", _SCOPE_NOVEL)
        assert _edges(hub, revive_chunk) == [
            ("survey", "found"),
            ("reconcile", "novel"),
            ("propose", "proposed"),
            ("deliver", "recorded"),
        ]
        revived = [p for p in hub.get("/api/garden-proposals").json()["proposals"] if p["routine_name"] == _ROUTINE]
        assert len(revived) == 4, revived
        (revival,) = [p for p in revived if p["closure"] is None and p["class"] == "tweak"]
        assert tweak_proposal["proposal_id"] in revival["body"], revival
        assert revival["findings"] == []

        # -- clean: survey straight to deliver; the empty delta's datapoint recorded --
        clean_chunk = run("clean", _SCOPE_CLEAN)
        assert _edges(hub, clean_chunk) == [("survey", "empty"), ("deliver", "recorded")]
        clean_delta = hub.get(f"/api/runs/{clean_chunk}").json()
        (clean_set,) = clean_delta["sets"]
        assert clean_set["added"] == [] and clean_set["observed"] == [] and clean_set["gone"] == []
        assert clean_set["revisions"] == {}
        assert clean_set["measurement"] == f"e2e ideation sweep of {_SCOPE_CLEAN}: 0 flagged, 0 proposed"

        # -- none: propose declines; the republished delta still delivers -------------
        none_chunk = run("none", _SCOPE_NONE)
        assert _edges(hub, none_chunk) == [
            ("survey", "found"),
            ("reconcile", "novel"),
            ("propose", "none"),
            ("deliver", "recorded"),
        ]
        none_delta = hub.get(f"/api/runs/{none_chunk}").json()
        (none_set,) = none_delta["sets"]
        assert none_set["added"] == [] and none_set["observed"] == [] and none_set["gone"] == []
        assert none_set["measurement"] == f"e2e ideation sweep of {_SCOPE_NONE}: 0 proposed"
        assert not [p for p in hub.get("/api/garden-proposals").json()["proposals"] if _SCOPE_NONE in p["title"]]

        # -- invalid: the wrongly-scoped delta bounces to PROPOSE (never reconcile), and
        #    the corrected delta delivers once the addendum has actually threaded --------
        invalid_chunk = run("invalid", _SCOPE_INVALID)
        assert _edges(hub, invalid_chunk) == [
            ("survey", "found"),
            ("reconcile", "novel"),
            ("propose", "proposed"),
            ("deliver", "invalid"),
            ("propose", "proposed"),
            ("deliver", "recorded"),
        ]
        invalid_delta = hub.get(f"/api/runs/{invalid_chunk}").json()
        (invalid_set,) = invalid_delta["sets"]
        assert invalid_set["added"] == [] and invalid_set["observed"] == [] and invalid_set["gone"] == []

        # -- failure: the shim crashes deliver once; the `failure` addendum republishes the
        #    docket unchanged and the retry records it exactly once -----------------------
        failure_chunk = run("failure", _SCOPE_FAILURE)
        assert _edges(hub, failure_chunk) == [
            ("survey", "found"),
            ("reconcile", "novel"),
            ("propose", "proposed"),
            ("deliver", "failure"),
            ("propose", "proposed"),
            ("deliver", "recorded"),
        ]
        failure_proposals = [
            p for p in hub.get("/api/garden-proposals").json()["proposals"] if _SCOPE_FAILURE in p["title"]
        ]
        assert len(failure_proposals) == 3, failure_proposals
        assert not list(crash_dir.iterdir()), "the crash trigger was not consumed by the shim"
        failure_logs = [
            a
            for a in hub.get(f"/api/chunks/{failure_chunk}").json()["artifacts"]
            if "simulated delivery crash" in (a.get("content") or "")
        ]
        assert failure_logs, "the deliver exec log did not record the shim's crash"

        # -- escalate: two consecutive invalid bounces, then the loop bound asks instead
        #    of resubmitting a third time; the answer resolves the delta and it records ---
        escalate_chunk = run_via_ask(
            "escalate", _SCOPE_ESCALATE, _ANSWER_ESCALATE_RESOLVED, question_substr=_ESCALATE_QUESTION_SUBSTR
        )
        escalate_edges = _edges(hub, escalate_chunk)
        assert escalate_edges == [
            ("survey", "found"),
            ("reconcile", "novel"),
            ("propose", "proposed"),
            ("deliver", "invalid"),
            ("propose", "proposed"),
            ("deliver", "invalid"),
            ("propose", "proposed"),
            ("deliver", "recorded"),
        ], escalate_edges
        assert escalate_edges.count(("deliver", "invalid")) == 2, "the loop bound let a third bounce through"

        # -- undeclared axis, still undeclared: survey parks on `ask`; answered with the
        #    axis still missing, it reaches `undeclared` -> done, never touching deliver --
        undeclared_chunk = run_via_ask(
            "ask-undeclared", _SCOPE_UNDECLARED, _ANSWER_STILL_UNDECLARED, question_substr=_ASK_AXIS_SUBSTR
        )
        assert _edges(hub, undeclared_chunk) == [("survey", "undeclared")]

        # -- undeclared axis, now declared: the answer says the registry was updated, so
        #    survey re-resolves and sweeps (empty) instead of choosing `undeclared` again --
        declared_chunk = run_via_ask(
            "ask-declared", _SCOPE_DECLARED_LATER, _ANSWER_NOW_DECLARED, question_substr=_ASK_AXIS_SUBSTR
        )
        assert _edges(hub, declared_chunk) == [("survey", "empty"), ("deliver", "recorded")]

    # -- the load-bearing session policy, off the runner's own store: reconcile never
    #    shares survey's session, and propose resumes reconcile's ----------------------
    db_url = config.db_url
    by_node = _sessions_by_node(db_url, found_chunk)
    (survey_sid,) = by_node["survey"]
    (reconcile_sid,) = by_node["reconcile"]
    (propose_sid,) = by_node["propose"]
    assert reconcile_sid != survey_sid, "reconcile did not get cold eyes — it shares survey's session"
    assert propose_sid == reconcile_sid, "propose did not resume the match session holding the shortlist"
