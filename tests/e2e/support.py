"""Shared helpers for end-to-end scenarios."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx2
import yaml

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.graphs import PACKAGED
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop_wiring import LoopWiring
from tests.fleet_launchers import REPO, runner_api
from tests.fleet_world import PUSH_AND_DECLARE_SCRIPT, REPO_NAME, scripted_build_prompt
from tests.runner_fakes import daemon_store
from tests.runner_join import fleet_headers

_SCOPE_FOUND = "garden-found"
_UNKNOWN_FINDING_ID = "fin_0" + "Z" * 25


def _common() -> str:
    return (
        "import json, os, pathlib, subprocess\n"
        "chunk_id = os.environ['BLIZZARD_CHUNK_ID']\n"
        "def sh(*args, inp=None):\n"
        "    return subprocess.run(list(args), input=inp, check=True, capture_output=True, text=True).stdout\n"
        "item = json.loads(sh('blizzard', 'runner', 'work-items', chunk_id))['items'][0]\n"
        "lines = item['body'].splitlines()\n"
        "path = next(l.split('=', 1)[1] for l in lines if l.startswith('path='))\n"
        "scope = next(l for l in lines if l.startswith('Scope:')).split()[1]\n"
        "def publish(name, content):\n"
        "    sh('blizzard', 'runner', 'artifact', 'create', '--name', name, inp=content)\n"
        "def rows():\n"
        "    return json.loads(sh('blizzard', 'runner', 'garden', 'findings'))\n"
        "def bucket():\n"
        "    # ops and matches target only rows under this run's own scope\n"
        "    return [r for r in rows() if r['scope_slug'] == scope and r['live']]\n"
        "addendum_marker = pathlib.Path(f'.garden-addendum-{chunk_id}')\n"
    )


_SURVEY = (
    "rev = sh('git', '-C', 'toy-api', 'rev-parse', 'HEAD').strip()\n"
    "fmt = sh('blizzard', 'runner', 'artifact', 'get', '--scope', 'system', 'garden/finding-format', '--content')\n"
    "assert 'FindingDelta' in fmt, 'system-scope finding format did not resolve'\n"
    "if path in ('found', 'invalid', 'virgin'):\n"
    "    cands = [\n"
    "        {'ref': 'F1', 'class': 'stale-docstring', 'locus': 'src/app.py:1', 'summary': 'first weed'},\n"
    "        {'ref': 'F2', 'class': 'stale-docstring', 'locus': 'src/app.py:2', 'summary': 'second weed'},\n"
    "    ]\n"
    "elif path == 'excessive':\n"
    "    cands = [{'ref': 'F1', 'class': 'excessive-scope', 'locus': scope,\n"
    "              'summary': 'roughly four hundred instances across the scope'}]\n"
    "elif path == 'no-strategy':\n"
    "    # As `survey.md` directs when the registry declares no entry for the axis: one\n"
    "    # bail-out candidate naming the registry and the axis, nothing else.\n"
    "    cands = [{'ref': 'F1', 'class': 'undeclared-axis', 'locus': 'gardening-axes registry',\n"
    "              'summary': 'the routine axis has no declared registry entry'}]\n"
    "else:\n"
    "    cands = []\n"
    "measurement = f'e2e sweep of {scope}: {len(cands)} flagged'\n"
    "publish('survey', json.dumps({'scope': scope, 'revisions': {'toy-api': rev},\n"
    "                              'measurement': measurement, 'candidates': cands}))\n"
    "# The skeleton every sweep publishes; reconcile's assembled delta supersedes it on\n"
    "# every path where reconcile runs, and on `clean` it is the delta delivered.\n"
    "publish('delta', json.dumps({'scope': scope, 'revisions': {'toy-api': rev},\n"
    "                             'measurement': measurement, 'findings': []}))\n"
)
_SURVEY_JUDGEMENT = (
    "choice = {'found': 'found', 'invalid': 'found', 'virgin': 'found', 'excessive': 'excessive',\n"
    "          'no-strategy': 'no-strategy', 'clean': 'clean'}[path]\n"
    "verdict(choice, 'scripted sweep')\n"
)
_RECONCILE = (
    "envelope = json.loads(sh('blizzard', 'runner', 'artifact', 'get', 'survey', '--content'))\n"
    "live = bucket()\n"
    "if path == 'virgin':\n"
    "    # the bucket is routine-wide: `found`'s findings are shown under `found`'s own scope\n"
    f"    assert any(r['scope_slug'] == {_SCOPE_FOUND!r} for r in rows()), 'neighbour findings not shown'\n"
    "if path == 'invalid' and not addendum_marker.exists():\n"
    "    delta = {'scope': scope, 'revisions': envelope['revisions'], 'measurement': envelope['measurement'],\n"
    f"             'findings': [{{'op': 'observed', 'id': {_UNKNOWN_FINDING_ID!r}}}]}}\n"
    "else:\n"
    "    ops = []\n"
    "    for cand in envelope['candidates']:\n"
    "        match = next((f for f in live if f['class'] == cand['class'] and f['locus'] == cand['locus']), None)\n"
    "        if match:\n"
    "            ops.append({'op': 'observed', 'id': match['finding_id']})\n"
    "        else:\n"
    "            # `ref` carried through from the candidate: this addition's own\n"
    "            # submission-local name, citable by propose in this same delivery.\n"
    "            ops.append({'op': 'add', 'class': cand['class'], 'locus': cand['locus'],\n"
    "                        'summary': cand['summary'], 'ref': cand['ref']})\n"
    "    delta = {'scope': scope, 'revisions': envelope['revisions'], 'measurement': envelope['measurement'],\n"
    "             'findings': ops}\n"
    "publish('delta', json.dumps(delta))\n"
)
_RECONCILE_JUDGEMENT = "verdict('nothing-to-propose' if addendum_marker.exists() else 'converged', 'scripted match')\n"
_RECONCILE_FROM_DELIVER = (
    "failure = sh('blizzard', 'runner', 'artifact', 'get', 'garden-delivery-failure', '--content')\n"
    "assert 'fin_' in failure, f'failure artifact does not name the rejected id: {failure!r}'\n"
    "good = {'scope': scope, 'revisions': envelope['revisions'], 'measurement': envelope['measurement'],\n"
    "        'findings': [{'op': 'observed', 'id': f['finding_id']} for f in live]}\n"
    "publish('delta', json.dumps(good))\n"
    "addendum_marker.write_text('threaded')\n"
)
_PROPOSE = (
    "fmt = sh('blizzard', 'runner', 'artifact', 'get', '--scope', 'system', 'garden/proposal-format', '--content')\n"
    "assert 'GardenProposalCandidate' in fmt, 'system-scope proposal format did not resolve'\n"
    "live = bucket()\n"
    "if path == 'virgin':\n"
    "    delta = json.loads(\n"
    "        sh('blizzard', 'runner', 'artifact', 'get', 'delta', '--node', 'reconcile', '--content'))\n"
    "    own_refs = [op['ref'] for op in delta['findings'] if op['op'] == 'add']\n"
    "    docket = [{'ref': 'P1', 'class': 'handoff', 'title': f'hand {scope} out of the fleet',\n"
    "               'body': 'scripted proposal citing the refs this very run minted',\n"
    "               'findings': own_refs}]\n"
    "elif path == 'invalid' or not live:\n"
    "    docket = []\n"
    "else:\n"
    "    docket = [{'ref': 'P1', 'class': 'handoff', 'title': f'hand {scope} out of the fleet',\n"
    "               'body': 'scripted proposal citing every live finding in the bucket',\n"
    "               'findings': [f['finding_id'] for f in live]}]\n"
    "publish('docket', json.dumps(docket))\n"
)
_PROPOSE_JUDGEMENT = (
    "verdict('proposed' if (path == 'virgin' or (path != 'invalid' and bucket())) else 'none',\n"
    "        'scripted propose')\n"
)


def _scripted_garden_graph_yaml() -> str:
    """Keep the packaged graph intact while replacing its prompts with scripts."""
    body = PACKAGED.named("garden-routine").body
    common = _common()
    nodes: Any = body["nodes"]
    nodes["survey"]["prompt"] = common + _SURVEY
    nodes["survey"]["judgement"]["prompt"] = common + _SURVEY_JUDGEMENT
    nodes["reconcile"]["prompt"] = common + _RECONCILE
    nodes["reconcile"]["judgement"]["prompt"] = common + _RECONCILE_JUDGEMENT
    nodes["propose"]["prompt"] = common + _PROPOSE
    nodes["propose"]["judgement"]["prompt"] = common + _PROPOSE_JUDGEMENT
    nodes["deliver"]["judgement"]["choices"]["invalid"]["prompt_addendum"] = _RECONCILE_FROM_DELIVER
    return yaml.safe_dump(body, sort_keys=False)


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
    import yaml

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


def _ingest_promote_claim(forge: httpx2.Client, hub: httpx2.Client, title: str) -> tuple[str, str]:
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": title, "body": "cost render chunk"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    registered = hub.post(
        "/api/fleet/runners",
        json={"name": "r1", "workspace_id": "w1", "capabilities": [{"harness_id": "claude", "default": True}]},
        headers=fleet_headers(hub),
    )
    assert registered.status_code == 201, registered.text
    claim = hub.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "workspace_id": "w1", "environment_ids": ["e"]},
        headers=fleet_headers(hub),
    )
    assert claim.status_code == 201, claim.text
    return chunk_id, claim.json()["envelope"]["node"]["node_id"]


def _push_usage(
    hub: httpx2.Client,
    *,
    chunk_id: str,
    node_id: str,
    seq: int,
    cost_usd: float | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_create_tokens: int = 0,
    estimated_cost_usd: float | None = None,
) -> None:
    payload = {
        "chunk_id": chunk_id,
        "node_id": node_id,
        "epoch": 1,
        "kind": "spawn",
        "model": "claude-opus-4-8",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_create_tokens": cache_create_tokens,
        "cost_usd": cost_usd,
    }
    if estimated_cost_usd is not None:
        payload["estimated_cost_usd"] = estimated_cost_usd
    resp = hub.post(
        "/api/fleet/events",
        json={"facts": [{"seq": seq, "kind": "usage.recorded", "payload": payload}]},
        headers=fleet_headers(hub),
    )
    assert resp.status_code == 200, resp.text
