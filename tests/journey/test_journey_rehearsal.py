"""The capstone: the MVP acceptance journey as one committed, repeatable rehearsal.

This is ``blizzard-discovery:/product/mvp.md`` — *the* acceptance journey — driven end
to end over the real fleet, faithfully mock-bound. It is the whole story in one test,
not a slice: five issues in, a night of autonomous work with a reboot in the middle, and
the morning-after assertions taken verbatim from the journey prose.

**The setup (journey ¶1).** Five issues are filed across the fixture workspace's two
repos (``toy-api`` + ``toy-web``) at the mock forge and ingested *by id* — each mints a
chunk. Two related ones are **grouped** into a single chunk through the same
``POST /chunks/{id}/group`` the board's Group control calls, and that grouped chunk — the
riskiest, because it spans both repos — is **moved to the top** of the ready queue through
``POST /queue/reorder`` (the board's Prioritize control). The queue peek proves both took.

**The night (journey ¶1-2).** The runner is a **real** ``blizzard runner host`` daemon
and the hub a **real** ``blizzard hub host`` daemon (the systemd units' ``ExecStart`` — see
``packaging/systemd/``); the fleet works the four chunks autonomously. Behaviour is *the
prompt is the program*: one shared ``build → review → deliver`` graph whose nodes read each
chunk's own PM item **through the hub pass-through** (``blizzard runner pm-items`` — MVP
criterion 1) and act on a directive in the issue body. So the fleet exhibits, unattended:

* the **grouped** chunk builds a real change in *both* repos, passes review, and lands on
  both bare mains (grouping + multi-repo serial delivery — criteria 11/13);
* one chunk's **review fails once**, carries its findings asset + the fail-edge
  ``prompt_addendum`` back into build's re-entry envelope, and lands on the second pass
  (criterion 9);
* one worker hits an **undecidable choice**, runs the real ``blizzard runner ask`` and
  exits; its chunk parks ``waiting_on_human`` (criterion 7);
* one chunk **genuinely fails** — every attempt exits verdict-less — exhausts its retry
  budget and escalates to ``needs_human`` with a pasteable takeover command (criterion 6).

**The reboot (journey ¶3).** Mid-run — while work is genuinely in flight — *both* daemons
are ``SIGKILL``ed and restarted through the same migrate-then-host path the systemd units
declare. The facts-level invariant checker is green the instant after the crash, and the
fleet continues: every chunk resumes at exactly the node the hub last recorded (criterion
4 is the exhaustive proof; here it is the journey's "it didn't matter" clause).

**The morning after (journey ¶2, verbatim).** ``blizzard hub answer`` resumes the parked
chunk with no takeover, and it lands. The failed chunk's escalation command, run
**verbatim**, drops into and resumes the stuck agent's session. Then: the succeeded chunks
merged to bare ``main`` via the default graph; the full history + artifacts are visible at
the hub API (the same facts the board renders); the asked chunk resumed without takeover;
nothing was worked twice (every landed file reachable from bare ``main`` exactly once); no
environment is orphaned (``blizzard dev check-invariants`` clean); and ``blizzard hub
status`` tells the truth about every chunk.

Gated like the crash sweep — needs the sibling ``blizzard-mock`` worktree, a local winter
source, and ``BLIZZARD_JOURNEY=1`` (see ``conftest.py``). Reproduce it with::

    BLIZZARD_JOURNEY=1 uv run pytest -m journey

Determinism: the fixture is re-minted from clean each run and every phase gates on a
*derived, latched* hub state (``waiting_on_human`` / ``needs_human`` / ``done``) rather than
on timing, so the rehearsal is repeatable — run it twice, it is green twice.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from blizzard.foundation.store.invariants import check_invariants
from blizzard.hub.config import HubConfig, PmSourceConfig
from blizzard.runner.config import RunnerConfig
from tests.crash.support import (
    OWNER,
    PM_TOKEN_ENV,
    await_http,
    forge_daemon,
    free_port,
    git_bare,
    mock_bin_dir,
    start_hub,
    start_runner,
    terminate,
    wait_status,
    winter_source,
    write_runner_config,
)

pytestmark = [
    pytest.mark.journey,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_JOURNEY") != "1",
        reason="capstone journey rehearsal needs the live stack; set BLIZZARD_JOURNEY=1 (see module docstring)",
    ),
]

FIXTURE_ENV = "journey"
# A fleet of envs so parked / escalated chunks (which retain their env for resume /
# takeover) never starve the chunks still building; max_agents caps concurrent workers.
RUNNER_ENVS = ("e1", "e2", "e3", "e4")
MAX_AGENTS = 2

# The two fixture repos (blizzard-mock/fixture_workspace/seed.py). The mock forge's git
# backend qualifies bare artifacts with OWNER, and both origins are minted by the fixture.
API_REPO = "toy-api"
WEB_REPO = "toy-web"


def _pm_sources(forge_port: int) -> tuple[PmSourceConfig, ...]:
    """Two ``[[pm_source]]`` bindings (D-108/D-109) — one per fixture repo — since the
    journey files issues across both. This is the case that proves the D-109
    repo-matching resolver: a first-entry shim would fetch half these issues from the
    wrong repo the moment two sources are configured (the Phase 1 finale's ``alpha#7``
    lying-label bug)."""
    api_base = f"http://127.0.0.1:{forge_port}"
    return (
        PmSourceConfig(
            name=API_REPO, provider="github", repo=f"{OWNER}/{API_REPO}", token_env=PM_TOKEN_ENV, api_base=api_base
        ),
        PmSourceConfig(
            name=WEB_REPO, provider="github", repo=f"{OWNER}/{WEB_REPO}", token_env=PM_TOKEN_ENV, api_base=api_base
        ),
    )


# --------------------------------------------------------------------------- #
# The shared build → review → deliver graph — the prompt is the program.
#
# Every node reads the chunk's own PM item through the runner→hub pass-through and
# branches on a ``KEY=value`` directive in the issue body, so a single graph drives four
# different journeys. ``.behavior`` is written by the build spawn into the env workdir
# (which persists across the chunk's node-steps) so the judgement / review turns — and the
# review's re-entry — read the same behaviour without another fetch.
# --------------------------------------------------------------------------- #

_BUILD_PROMPT = f"""\
import os, json, subprocess, pathlib


def _commit(repo, msg):
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", repo,
         "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",
         "commit", "-m", msg],
        check=True,
    )


chunk_id = os.environ["BLIZZARD_CHUNK_ID"]
# MVP criterion 1: the worker reads its issue ONLY through the hub pass-through.
_raw = subprocess.run(
    ["blizzard", "runner", "pm-items", chunk_id], check=True, capture_output=True, text=True
).stdout
_item = json.loads(_raw)["items"][0]
_body = _item["body"]


def _field(key, default=""):
    for line in _body.splitlines():
        s = line.strip()
        if s.startswith(key + "="):
            return s[len(key) + 1:].strip()
    return default


behavior = _field("BEHAVIOR", "clean")
repos = [r.strip() for r in _field("REPOS", {API_REPO!r}).split(",") if r.strip()]
fname = _field("FILE", "LANDED.md")
pathlib.Path(".behavior").write_text(behavior)

if behavior == "ask":
    if answer() is None:
        ask("Which API style should the endpoint use?", ["rest", "graphql"])
    # On resume the human's answer script (not this prompt) runs and makes the commit.
elif behavior == "escalate":
    pass  # no work; the judgement below emits no verdict -> every attempt fails
else:  # clean / review-fail: a real change in each repo (append so a re-build commits again)
    for repo in repos:
        p = pathlib.Path(repo) / fname
        p.write_text((p.read_text() if p.exists() else "") + "landed " + behavior + "\\n")
        _commit(repo, "feat: " + behavior + " land in " + repo)
"""

# The fail-edge addendum, inlined onto build's re-entry prompt (same namespace: ``repos`` /
# ``_commit`` / ``pathlib`` are already bound). Its committed marker reaches bare ``main``
# ONLY if the findings threaded back through the envelope (criterion 9, D-089).
_REVIEW_ADDENDUM = """\
for repo in repos:
    pathlib.Path(repo, "REVIEW_ADDRESSED.md").write_text("addressed the review findings\\n")
    _commit(repo, "fix: address review findings")
"""

_BUILD_JUDGEMENT = """\
import pathlib
b = pathlib.Path(".behavior").read_text().strip() if pathlib.Path(".behavior").exists() else "clean"
if b == "escalate":
    pass  # NO verdict() -> verdict-less -> a failed attempt (D-009)
else:
    verdict("pass", "build checks are green")
"""

_REVIEW_PROMPT = "pass\n"  # the verdict is elicited on the judgement resume

_REVIEW_JUDGEMENT = """\
import pathlib
b = pathlib.Path(".behavior").read_text().strip() if pathlib.Path(".behavior").exists() else "clean"
if b == "review-fail":
    m = pathlib.Path(".review-count")
    n = (int(m.read_text()) if m.exists() else 0) + 1
    m.write_text(str(n))
    if n == 1:
        verdict("fail", "BLOCKING: guard the empty input before delivery")
    else:
        verdict("pass", "findings addressed; ready to deliver")
else:
    verdict("pass", "cold-eyes review: clean; ready to deliver")
"""

# The answer the operator types at the hub (criterion 7). It arrives as the resume message
# the parked session execs, and it is what lands the asked chunk's change on toy-web.
_ANSWER_SCRIPT = (
    "import subprocess, pathlib\n"
    f"repo = {WEB_REPO!r}\n"
    '(pathlib.Path(repo) / "LANDED-ask.md").write_text("landed after the human answered\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: resolve the ask and land"],\n'
    "    check=True,\n"
    ")\n"
)


def _graph_yaml() -> str:
    import yaml

    graph = {
        "name": "default-delivery",  # reused by name on ingest (D-081)
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _BUILD_PROMPT,
                "judgement": {
                    "prompt": _BUILD_JUDGEMENT,
                    "choices": {"pass": {"description": "Committed and green.", "to": "review"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "review": {
                "executor": "runner",
                "prompt": _REVIEW_PROMPT,
                "session": "fresh",
                "produces": ["review-findings"],
                "judgement": {
                    "prompt": _REVIEW_JUDGEMENT,
                    "choices": {
                        "pass": {"description": "Passes cold-eyes review.", "to": "deliver"},
                        "fail": {
                            "description": "Blocking issues found.",
                            "to": "build",
                            "prompt_addendum": _REVIEW_ADDENDUM,
                        },
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {"executor": "hub", "mode": "merge-to-main"},
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


# --------------------------------------------------------------------------- #
# The five issues (their bodies carry the per-chunk behaviour directive).
# --------------------------------------------------------------------------- #


def _issue_body(behavior: str, repos: str, fname: str) -> str:
    return f"BEHAVIOR={behavior}\nREPOS={repos}\nFILE={fname}\n"


def _blizzard_bin(name: str) -> str:
    return str(Path(sys.executable).parent / name)


def _file_hub(forge: httpx.Client, repo: str, title: str, body: str) -> tuple[str, str]:
    """File an issue and return its ``{source, ref}`` pointer (D-107) — ``repo`` is the
    configured source's own name (``_pm_sources``), ``ref`` its issue number."""
    issue = forge.post(f"/repos/{OWNER}/{repo}/issues", json={"title": title, "body": body})
    assert issue.status_code == 201, issue.text
    return repo, str(issue.json()["number"])


def _ingest(hub: httpx.Client, pointer: tuple[str, str]) -> str:
    source, ref = pointer
    resp = hub.post("/api/chunks", json={"tokens": [f"{source}:{ref}"]})
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    # Ingest rests not-ready (D-103); promote so the fleet claims it as the journey expects.
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    return chunk_id


def _restart_daemons(*, hub_dir: Path, forge_port: int, hub_port: int, hub: httpx.Client) -> subprocess.Popen[str]:
    """Bring the hub back through the systemd units' migrate-then-host path (the runner is
    restarted by the caller). Returns the fresh, healthy hub process."""
    hub_proc = start_hub(
        hub_dir, forge_port=forge_port, port=hub_port, crash_point=None, pm_sources=_pm_sources(forge_port)
    )
    await_http(hub, "/api/health", proc=hub_proc)
    return hub_proc


def _assert_invariants(runner_dir: Path, hub_dir: Path, *, when: str) -> None:
    violations = check_invariants(
        runner_db_url=RunnerConfig.load(runner_dir).db_url,
        hub_db_url=HubConfig.load(hub_dir).db_url,
    )
    assert not violations, f"invariant violations {when}:\n" + "\n".join(str(v) for v in violations)


def test_the_acceptance_journey_end_to_end(tmp_path: Path) -> None:
    """The whole MVP journey — setup, an autonomous night with a reboot, morning after."""
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    source = winter_source()
    if source is None:
        pytest.skip("no local winter source (set BLIZZARD_MOCK_WINTER_SOURCE)")

    # ------------------------------------------------------------------ #
    # Mint a fresh, disposable fixture world (bare origins + a real winter workspace).
    # ------------------------------------------------------------------ #
    scratch = tmp_path / "scratch"
    subprocess.run(
        [
            str(bin_dir / "blizzard-mock-fixture"), "reset",
            "--env", FIXTURE_ENV, "--scratch-root", str(scratch), "--winter-source", str(source),
        ],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"
    (workspace / ".blizzard-mock-harness-fence").write_text("journey fence marker\n")

    hub_dir, runner_dir = tmp_path / "hub", tmp_path / "runner"
    forge_port, hub_port, runner_port = free_port(), free_port(), free_port()
    hub = httpx.Client(base_url=f"http://127.0.0.1:{hub_port}", timeout=30.0)
    hub_url = f"http://127.0.0.1:{hub_port}"

    hub_proc: subprocess.Popen[str] | None = None
    runner_proc: subprocess.Popen[str] | None = None
    with forge_daemon(bin_dir, origins, forge_port) as forge:
        try:
            hub_proc = start_hub(
                hub_dir, forge_port=forge_port, port=hub_port, crash_point=None, pm_sources=_pm_sources(forge_port)
            )
            await_http(hub, "/api/health", proc=hub_proc)
            assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201

            # ---------------------------------------------------------- #
            # Journey ¶1 — five issues across the repos, ingested by id.
            # ---------------------------------------------------------- #
            grp_api = _ingest(
                hub,
                _file_hub(
                    forge, API_REPO, "grouped: pagination", _issue_body("clean", "toy-api,toy-web", "LANDED-grouped.md")
                ),
            )
            grp_web = _ingest(
                hub,
                _file_hub(
                    forge, WEB_REPO, "grouped: pagination UI", _issue_body("clean", "toy-web", "LANDED-grouped-2.md")
                ),
            )
            rev = _ingest(
                hub,
                _file_hub(forge, WEB_REPO, "restyle button", _issue_body("review-fail", "toy-web", "BUILD-review.md")),
            )
            ask = _ingest(
                hub, _file_hub(forge, WEB_REPO, "config parser", _issue_body("ask", "toy-web", "LANDED-ask.md"))
            )
            esc = _ingest(hub, _file_hub(forge, API_REPO, "flaky login",
                                         _issue_body("escalate", "toy-api", "LANDED-escalate.md")))  # fmt: skip

            # Group the two related ones into one chunk (grp_api is the survivor, so its
            # issue body drives the merged chunk's behaviour), then move it to the top.
            grouped = hub.post(f"/api/chunks/{grp_api}/group", json={"merge_chunk_ids": [grp_web]})
            assert grouped.status_code == 200, grouped.text
            assert grouped.json()["chunk_id"] == grp_api
            assert grp_web in grouped.json()["merged_chunk_ids"]

            reordered = hub.post("/api/queue/reorder", json={"chunk_id": grp_api, "position": 0})
            assert reordered.status_code == 200, reordered.text
            peek = hub.get("/api/queue/peek").json()["entries"]
            assert peek[0]["chunk_id"] == grp_api, f"riskiest not at the top of the queue: {peek}"
            assert grp_web not in {e["chunk_id"] for e in peek}, "the merged-away chunk still shows in the queue"

            all_chunks = {"grouped": grp_api, "review": rev, "ask": ask, "escalate": esc}

            # ---------------------------------------------------------- #
            # Journey ¶1-2 — the fleet works overnight (real host daemons).
            # ---------------------------------------------------------- #
            write_runner_config(runner_dir, workspace=workspace, bin_dir=bin_dir, hub_port=hub_port, port=runner_port)
            _widen_runner_pool(runner_dir)
            runner_proc = start_runner(runner_dir, crash_point=None)

            # Let the fleet work the night through to where it needs a human: the two
            # landing chunks reach ``done`` on their own, the asked chunk parks
            # ``waiting_on_human`` (its lease held, reap clock stopped), and the failing one
            # escalates to ``needs_human``. Each is a *latched* hub state, so this gate is
            # deterministic — no reliance on timing.
            assert wait_status(hub, grp_api, {"done"}, timeout=300.0) == "done"
            assert wait_status(hub, rev, {"done"}, timeout=300.0) == "done"
            assert wait_status(hub, ask, {"waiting_on_human"}, timeout=300.0) == "waiting_on_human"
            assert wait_status(hub, esc, {"needs_human"}, timeout=300.0) == "needs_human"

            # ---------------------------------------------------------- #
            # Journey ¶3 — at some point in the night the machine reboots: SIGKILL BOTH the
            # colocated hub and the runner supervisor, then bring them back through the same
            # migrate-then-host path the systemd units declare (packaging/systemd/). Two
            # chunks are still mid-journey across the reboot — one parked on an open question
            # (its lease held), one escalated — so this proves the journey's "it didn't
            # matter" clause: a full-machine reboot leaves every chunk at exactly the node
            # the hub last recorded. (The exhaustive per-boundary kill-9 recovery proof is
            # the crash sweep, tests/crash/.)
            # ---------------------------------------------------------- #
            runner_proc.kill()
            runner_proc.wait(timeout=15)
            hub_proc.kill()
            hub_proc.wait(timeout=15)

            # The durable facts are consistent the instant after the crash.
            _assert_invariants(runner_dir, hub_dir, when="immediately after the mid-run reboot")

            hub_proc = _restart_daemons(hub_dir=hub_dir, forge_port=forge_port, hub_port=hub_port, hub=hub)
            runner_proc = start_runner(runner_dir, crash_point=None)

            # Recovery: every chunk is still at exactly the node the hub last recorded — the
            # reboot changed nothing. The parked lease was NOT reaped (its clock is stopped),
            # and the two terminal chunks stayed terminal.
            recovered = {c: hub.get(f"/api/chunks/{c}").json()["status"] for c in all_chunks.values()}
            assert recovered[grp_api] == "done" and recovered[rev] == "done", recovered
            assert recovered[ask] == "waiting_on_human", f"the parked chunk did not survive the reboot: {recovered}"
            assert recovered[esc] == "needs_human", f"the escalation did not survive the reboot: {recovered}"
            _assert_invariants(runner_dir, hub_dir, when="right after the reboot recovery")

            # ---------------------------------------------------------- #
            # Journey ¶2 — the human loop, after the reboot.
            # ---------------------------------------------------------- #
            # The failed chunk's takeover command, run VERBATIM, resumes the stuck agent's
            # session (its persisted state advances — not just that the string exists).
            escalation = hub.get(f"/api/chunks/{esc}").json()["escalation"]
            assert escalation and escalation["takeover_command"], "no pasteable takeover command on the escalation"
            takeover = escalation["takeover_command"]
            session_before = _session_after_takeover(workspace, takeover, bin_dir)

            # The parked chunk is answered at the hub — no takeover — and resumes to done,
            # proving the open question survived the hub restart.
            question = hub.get(f"/api/chunks/{ask}").json()["questions"][0]
            ask_session_id = question["session_id"]
            answered = subprocess.run(
                [_blizzard_bin("blizzard"), "hub", "answer", question["question_id"], _ANSWER_SCRIPT,
                 "--by", "alice", "--url", hub_url],
                capture_output=True, text=True,
            )  # fmt: skip
            assert answered.returncode == 0, f"hub answer failed:\n{answered.stderr}"

            # ---------------------------------------------------------- #
            # Journey ¶2 — the morning after. Every succeeded chunk lands.
            # ---------------------------------------------------------- #
            for name in ("grouped", "review", "ask"):
                got = wait_status(hub, all_chunks[name], {"done"}, timeout=300.0)
                assert got == "done", f"{name} chunk did not reach done (last {got!r})"
            assert hub.get(f"/api/chunks/{esc}").json()["status"] == "needs_human"

            # No environment orphaned; nothing worked twice — the facts-level checker is green.
            _assert_invariants(runner_dir, hub_dir, when="after the whole journey converged")
            _assert_check_invariants_cli(runner_dir, hub_dir)

            _assert_morning_after(hub, all_chunks, workspace, ask_session_id, session_before, origins)
            _assert_hub_status_truthful(hub_url, all_chunks)
        finally:
            hub.close()
            terminate(runner_proc)
            terminate(hub_proc)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _widen_runner_pool(runner_dir: Path) -> None:
    """Re-persist the runner config with the journey's env pool + agent cap.

    ``write_runner_config`` (crash support) pins a single ``e1`` env; the journey needs a
    pool so parked/escalated chunks (which keep their env for resume/takeover) do not
    starve the chunks still building.
    """
    import dataclasses

    config = RunnerConfig.load(runner_dir)
    config = dataclasses.replace(config, workspace_envs=RUNNER_ENVS, max_agents=MAX_AGENTS)
    config.config_path.write_text(config.to_toml())


def _session_state_path(workspace: Path, session_id: str) -> Path:
    return workspace / ".blizzard-mock-harness" / "sessions" / f"{session_id}.json"


def _session_after_takeover(workspace: Path, takeover: str, bin_dir: Path) -> dict[str, object]:
    """Run the escalation's takeover command VERBATIM (a human pasting it) and assert it
    resumed the stuck session — its persisted state advanced a turn and recorded the message.
    Returns the pre-takeover state so the caller can re-confirm the delta held."""
    import os
    import re
    import uuid

    match = re.search(r"--resume (\S+)", takeover)
    assert match is not None, f"takeover command carries no --resume session: {takeover!r}"
    session_id = match.group(1)
    state_path = _session_state_path(workspace, session_id)
    assert state_path.is_file(), f"the parked session state should exist on disk: {state_path}"
    before = json.loads(state_path.read_text())

    marker = f"human-takeover-{uuid.uuid4().hex}"
    result = subprocess.run(
        takeover, shell=True, input=f"# {marker}\n", text=True, capture_output=True,
        env={**os.environ, "BLIZZARD_MOCK_HARNESS_FENCE": "1"},
    )  # fmt: skip
    assert result.returncode == 0, f"takeover command failed ({result.returncode}):\n{result.stderr}"

    after = json.loads(state_path.read_text())
    assert after["turns"] == before["turns"] + 1, f"takeover did not advance the session turn: {after}"
    assert any(marker in r for r in after["resumes"]), f"takeover message not recorded on resume: {after['resumes']}"
    return before


def _assert_check_invariants_cli(runner_dir: Path, hub_dir: Path) -> None:
    """The operator's own ``blizzard dev check-invariants`` over both stores exits clean."""
    result = subprocess.run(
        [_blizzard_bin("blizzard"), "dev", "check-invariants",
         "--runner-dir", str(runner_dir), "--hub-dir", str(hub_dir)],
        capture_output=True, text=True,
    )  # fmt: skip
    assert result.returncode == 0, f"check-invariants reported orphans/violations:\n{result.stdout}\n{result.stderr}"


def _assert_morning_after(
    hub: httpx.Client,
    chunks: dict[str, str],
    workspace: Path,
    ask_session_id: str,
    session_before: dict[str, object],
    origins: Path,
) -> None:
    api_bare = origins / f"{API_REPO}.git"
    web_bare = origins / f"{WEB_REPO}.git"

    # Git truth — the succeeded chunks merged to bare main via the default graph. The
    # grouped chunk landed a real change on BOTH repos (grouping + multi-repo delivery).
    for bare in (api_bare, web_bare):
        tree = git_bare(bare, "ls-tree", "-r", "--name-only", "main").split()
        assert "LANDED-grouped.md" in tree, f"grouped change missing from {bare.name} main:\n{tree}"
        # Nothing worked twice: the grouped file is reachable from main exactly once per repo.
        commits = [
            ln for ln in git_bare(bare, "log", "--oneline", "--", "LANDED-grouped.md").splitlines() if ln.strip()
        ]
        assert len(commits) == 1, f"LANDED-grouped.md landed {len(commits)} times on {bare.name} main"

    # The review-fail chunk: build ran TWICE (two lines) and the fail-edge addendum's marker
    # landed — git-truth proof the findings threaded back through the re-entry envelope.
    build_md = git_bare(web_bare, "show", "main:BUILD-review.md")
    assert build_md.count("landed review-fail") == 2, f"review-fail build did not run twice:\n{build_md}"
    web_tree = git_bare(web_bare, "ls-tree", "-r", "--name-only", "main").split()
    assert "REVIEW_ADDRESSED.md" in web_tree, f"fail-edge addendum did not land on web main:\n{web_tree}"
    assert "LANDED-ask.md" in web_tree, f"the answered chunk's change did not land:\n{web_tree}"

    # The failed chunk never delivered — its file is on no main.
    assert "LANDED-escalate.md" not in git_bare(api_bare, "ls-tree", "-r", "--name-only", "main").split()

    # Hub API — the full history + artifacts render (the same facts the board consumes).
    # History records opaque node ids + the choice on each edge; the artifacts carry the
    # node *name*. Together they show the chunk walked build -> review -> deliver -> done.
    grouped_detail = hub.get(f"/api/chunks/{chunks['grouped']}").json()
    choices = [t["choice_name"] for t in grouped_detail["history"]]
    assert len(grouped_detail["history"]) == 3, (
        f"grouped chunk did not walk all three edges: {grouped_detail['history']}"
    )
    assert "landed" in choices, f"grouped chunk never took the deliver->done (landed) edge: {choices}"
    build_artifacts = [
        a for a in grouped_detail["artifacts"] if a["kind"] == "git_commit" and a["node_name"] == "build"
    ]
    grouped_repos = {a.get("repo") for a in build_artifacts}
    assert {API_REPO, WEB_REPO} <= grouped_repos, (
        f"grouped chunk missing a per-repo build commit artifact: {grouped_repos}"
    )

    review_detail = hub.get(f"/api/chunks/{chunks['review']}").json()
    assert any(t["choice_name"] == "fail" for t in review_detail["history"]), "no review-fail step in the history"
    assert any(t["choice_name"] == "pass" for t in review_detail["history"]), "no passing step in the history"
    findings = [a for a in review_detail["artifacts"] if a["name"] == "review-findings" and a["kind"] == "asset"]
    assert findings and any("BLOCKING" in (a["content"] or "") for a in findings), "the findings asset is not exposed"

    # The asked chunk resumed WITHOUT takeover: its session recorded the human's answer
    # script and never the takeover marker (that marker only belongs to the escalated chunk).
    ask_state = json.loads(_session_state_path(workspace, ask_session_id).read_text())
    assert any("resolve the ask" in r for r in ask_state["resumes"]), f"answer not recorded on resume: {ask_state}"
    assert not any("human-takeover" in r for r in ask_state["resumes"]), "the asked chunk needed a takeover"
    # And the escalation's session is the one the takeover touched (distinct pre-state existed).
    assert "turns" in session_before


def _assert_hub_status_truthful(hub_url: str, chunks: dict[str, str]) -> None:
    """``blizzard hub status`` tells the truth about every chunk — derived, not written."""
    result = subprocess.run(
        [_blizzard_bin("blizzard"), "hub", "status", "--url", hub_url],
        capture_output=True, text=True,
    )  # fmt: skip
    assert result.returncode == 0, f"hub status failed:\n{result.stderr}"
    out = result.stdout
    expected = {
        chunks["grouped"]: "done",
        chunks["review"]: "done",
        chunks["ask"]: "done",
        chunks["escalate"]: "needs_human",
    }
    for chunk_id, status in expected.items():
        line = next((ln for ln in out.splitlines() if chunk_id in ln), None)
        assert line is not None, f"hub status omitted chunk {chunk_id}:\n{out}"
        assert status in line, f"hub status lied about {chunk_id} (want {status}): {line!r}"
