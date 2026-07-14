"""The acceptance loop — scenario 1 of the standing e2e smoke (verification.md).

ONE chunk travels the whole default lifecycle — ingest -> acquire -> mock-scripted
commit -> **review (scripted PASS)** -> deliver -> landed in the bare origin — and the
assertion holds at **both ends**: the commit is reachable from the bare origin's
``main`` (git truth) *and* the hub's facts derive the chunk ``done`` (fleet truth).
This is the P6 exit criterion of ``blizzard-discovery:/implementation/verification.md``,
extended in P7 (wave 1) to travel the full ``build -> review -> deliver`` default
shape (design/workflow-engine.md) — the review node is the new stop, and here it
passes on the first cold-eyes look, so the chunk lands without a re-build. The two
sibling e2e scenarios cover the review **fail** cycle (test_review_cycle_e2e) and the
retries-exhausted **escalation** to ``needs_human`` (test_escalation_e2e); the three
run together as ``mise run e2e``.

**Self-managed, zero-token, no-network.** The test mints its own disposable fixture
world and drives the real seams end to end, with no in-process shortcuts:

* a real, disposable **fixture workspace** (bare ``file://`` origins + a real winter
  workspace) minted by ``blizzard-mock``'s ``blizzard-mock-fixture`` scaffold;
* the real **mock GitHub forge** (``blizzard-mock-forge``) fronting those same bare
  origins — the single git truth;
* the real **hub** (``blizzard hub host``) over a fresh sqlite store, wired to the
  forge;
* the real **runner reconciliation loop**, driven one synchronous
  :func:`~blizzard.runner.loop.build.run_single_tick` pass at a time (the steppable
  driver, ``bzh:steppable-loop``), acquiring a fixture env, spawning the
  ``mock-claude-code`` façade whose scripted prompt makes a **real commit**, pushing
  the branch to the ``file://`` origin, and submitting the completion that drives the
  hub's deliver node to PR + merge the branch into bare ``main``.

It is the **e2e tier** (``bzh:`` verification tiers): it needs the full live stack and
the sibling ``blizzard-mock`` worktree, so it is **skipped unless ``BLIZZARD_E2E=1``**,
keeping the default ``pytest`` gate (unit + component) hermetic and token-free. It is
also skipped when the workspace layout it needs (a sibling ``blizzard-mock`` with a
synced virtualenv, and a local winter source) is not discoverable.

Reproduce it — from the ``blizzard`` worktree in a provisioned feature env — with::

    BLIZZARD_E2E=1 uv run pytest tests/e2e/test_acceptance_loop.py

(The workspace runs it under ``mise run e2e``; see the repo README.)
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import uvicorn

from blizzard.runner.app import build_hosted_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop.build import run_single_tick
from blizzard.runner.runtime import init_environment as init_runner_environment

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e acceptance loop needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

# The fixture project repo the loop drives and the owner the forge/hub address it
# under. The forge's git backend is permissive (``forge/internal/git_backend.py``):
# ``blizzard/toy-api`` resolves the flat ``origins/toy-api.git`` the fixture mints,
# and the hub qualifies the runner's bare ``toy-api`` artifact with this same owner
# (BZ_FORGE_OWNER -> github_forge._repo_path).
OWNER = "blizzard"
REPO_NAME = "toy-api"
REPO = f"{OWNER}/{REPO_NAME}"
# The env keying the disposable fixture world (outer). The runner acquires its own
# INNER env (``e1``, the runner's default pool) inside the fixture workspace.
FIXTURE_ENV = "e2e"
RUNNER_ENV = "e1"

# The scripted build-node prompt: *the prompt is the program*. It runs under the mock
# harness in the acquired env dir (which holds the repo worktrees as children), so it
# targets the ``toy-api`` worktree explicitly, writing a file and making a real commit
# on the env's branch. The runner then discovers that commit (HEAD ahead of base) and
# pushes it (design/runner/loop.md ADVANCE).
_BUILD_SCRIPT = (
    "import subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    '(pathlib.Path(repo) / "LANDED.md").write_text("landed by the mock harness\\n")\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: land a change from the mock harness"],\n'
    "    check=True,\n"
    ")\n"
)
# The judgement-resume prompt: also arrives as code (D-038). It emits the tagged
# ``<Choice>pass</Choice>`` the runner's adapter parses into the completion choice.
_JUDGEMENT_SCRIPT = "verdict('pass', 'the mock harness committed the change; checks are green')\n"

# The review node (design/workflow-engine.md): a fresh-session cold-eyes read that
# produces a ``review-findings`` asset and, here, PASSES on the first look — so the
# build commit travels straight to deliver with no re-build. The review base turn is a
# no-op (``pass``); the verdict is elicited on the judgement resume, whose assessment
# (the text after ``</Choice>``) becomes the produced asset's content (D-026/D-077).
_REVIEW_SCRIPT = "pass\n"
_REVIEW_JUDGEMENT = "verdict('pass', 'cold-eyes review: the committed change is clean; ready to deliver')\n"

# The pass-through scenario's distinctive PM item — a body + a comment whose exact text
# is asserted on the bare origin's main, so its presence there proves it travelled the
# whole layered pass-through (worker -> runner proxy -> hub -> forge) and back into the
# committed, landed change (MVP criterion 1, D-047/D-084).
_PM_BODY = "PASSTHROUGH-BODY: the widget flake reproduces under load"
_PM_COMMENT = "PASSTHROUGH-COMMENT: attached a failing repro in the linked gist"

# build turn (prompt-is-program): read the chunk's PM item through the runner's PM-item
# proxy — the *real* ``blizzard runner pm-items`` verb against the local API, chunk id
# from the spawn-injected ``BLIZZARD_CHUNK_ID`` — then commit the fetched body + comment
# so the pass-through's output lands as git truth.
_PM_BUILD_SCRIPT = (
    "import os, json, subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    "chunk_id = os.environ['BLIZZARD_CHUNK_ID']\n"
    "out = subprocess.run(\n"
    '    ["blizzard", "runner", "pm-items", chunk_id],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout\n"
    "item = json.loads(out)\n"
    "payload = item['body'] + '\\n' + '\\n'.join(item['comments']) + '\\n'\n"
    '(pathlib.Path(repo) / "LANDED.md").write_text(payload)\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: land the PM item fetched through the pass-through"],\n'
    "    check=True,\n"
    ")\n"
)


def _graph_yaml() -> str:
    """The scripted ``default-delivery`` graph — ``build -> review -> deliver``.

    Named ``default-delivery`` so the hub's lazy ``ensure_default`` (POST /chunks)
    reuses this pre-minted graph by name (D-081) instead of minting the packaged
    prose graph — the packaged prompts are LLM prose the mock cannot ``exec``. Mirrors
    the packaged default's shape (design/hub/graph-schema.md): a runner build, a
    fresh-session runner review that ``produces`` findings, and a hub deliver node.
    """
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _BUILD_SCRIPT,
                "judgement": {
                    "prompt": _JUDGEMENT_SCRIPT,
                    "choices": {
                        "pass": {
                            "description": "The change is committed and the node's checks are green.",
                            "to": "review",
                        }
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
                        "pass": {"description": "The change passes cold-eyes review.", "to": "deliver"},
                        "fail": {
                            "description": "Blocking issues found.",
                            "to": "build",
                            "prompt_addendum": "# address the review findings\n",
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
# Workspace-layout discovery (the sibling blizzard-mock worktree + winter source)
# --------------------------------------------------------------------------- #


def _blizzard_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _mock_bin_dir() -> Path | None:
    """The provisioned ``blizzard-mock`` virtualenv bin (sibling worktree), or None."""
    mock = _blizzard_root().parent / "blizzard-mock"
    bin_dir = mock / ".venv" / "bin"
    if (bin_dir / "blizzard-mock-fixture").is_file() and (bin_dir / "mock-claude-code").is_file():
        return bin_dir
    return None


def _winter_source() -> Path | None:
    """A local winter workspace (``.winter/config.toml`` + ``tools/winter-cli``) to clone."""
    explicit = os.environ.get("BLIZZARD_MOCK_WINTER_SOURCE")
    start = Path(explicit).resolve() if explicit else _blizzard_root()
    for directory in [start, *start.parents]:
        if (directory / ".winter" / "config.toml").is_file() and (directory / "tools" / "winter-cli").is_dir():
            return directory
    return None


# --------------------------------------------------------------------------- #
# Process helpers
# --------------------------------------------------------------------------- #


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _git_bare(bare: Path, *args: str) -> str:
    return subprocess.run(["git", "--git-dir", str(bare), *args], check=True, capture_output=True, text=True).stdout


def _await_http(proc: subprocess.Popen[str], client: httpx.Client, path: str, *, timeout: float = 40.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise AssertionError(f"process exited early ({proc.returncode}):\n{out}")
        with contextlib.suppress(httpx.HTTPError):
            if client.get(path).status_code == 200:
                return
        time.sleep(0.15)
    raise AssertionError(f"process did not answer {path} within {timeout}s")


@contextlib.contextmanager
def _forge(bin_dir: Path, origins: Path, port: int) -> Iterator[httpx.Client]:
    proc = subprocess.Popen(
        [str(bin_dir / "blizzard-mock-forge"), "--repos-dir", str(origins), "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15.0)
    try:
        _await_http(proc, client, "/healthz")
        yield client
    finally:
        client.close()
        _terminate(proc)


@contextlib.contextmanager
def _hub(hub_dir: Path, forge_port: int, port: int) -> Iterator[httpx.Client]:
    env = {
        **os.environ,
        "BZ_FORGE_URL": f"http://127.0.0.1:{forge_port}",
        "BZ_FORGE_OWNER": OWNER,
    }
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    subprocess.run([hub_bin, "init", str(hub_dir)], check=True, capture_output=True, text=True)
    proc = subprocess.Popen(
        [hub_bin, "host", "--dir", str(hub_dir), "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30.0)
    try:
        _await_http(proc, client, "/api/health")
        yield client
    finally:
        client.close()
        _terminate(proc)


def _terminate(proc: subprocess.Popen[str]) -> None:
    proc.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=10)
    if proc.poll() is None:
        proc.kill()


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


def test_acceptance_loop_one_chunk_ingest_to_landed(tmp_path: Path) -> None:
    """One chunk travels the whole lifecycle and derives ``done`` (verification.md)."""
    bin_dir = _mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    winter_source = _winter_source()
    if winter_source is None:
        pytest.skip("no local winter source (set BLIZZARD_MOCK_WINTER_SOURCE)")

    scratch = tmp_path / "scratch"
    # 1. Mint a fresh, disposable fixture world: bare file:// origins + a real winter
    #    workspace over them. `reset` re-mints from clean, so the test is repeatable.
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
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"
    origin_bare = origins / f"{REPO_NAME}.git"
    assert workspace.is_dir() and origin_bare.is_dir(), "fixture mint did not lay out the expected tree"

    # Fence the fixture tree so the mock harness will run (arbitrary code execution is
    # the feature, gated on a marker file + env var). The marker at the workspace root
    # covers every acquired env worktree under it via the engine's ancestor walk.
    (workspace / ".blizzard-mock-harness-fence").write_text("e2e fence marker\n")

    forge_port, hub_port = _free_port(), _free_port()
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        # Sanity: the forge sees the fixture's bare repo on default branch main.
        repo = forge.get(f"/repos/{REPO}")
        assert repo.status_code == 200, repo.text
        assert repo.json()["default_branch"] == "main"

        # 2. Pre-mint the scripted default graph (reused by name on ingest), then file
        #    an issue on the forge and ingest its pointer -> a `ready` chunk.
        minted = hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()})
        assert minted.status_code == 201, minted.text

        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "land a change", "body": "the acceptance chunk"})
        assert issue.status_code == 201, issue.text
        issue_number = issue.json()["number"]

        ingested = hub.post(
            "/api/chunks",
            json={"pointers": [{"provider": "github", "url": f"{REPO}/issues/{issue_number}"}]},
        )
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "ready"

        # 3. Drive the runner loop one synchronous tick at a time until the chunk lands.
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
        status = _drive_until_done(config, hub, chunk_id, fenced)

        # 4a. Fleet truth — the hub's facts derive the chunk done.
        assert status == "done", f"chunk did not reach done (last status {status!r})"

        # 4b. The forge reports the PR merged (the delivery seam ran for real).
        pulls = forge.get(f"/repos/{REPO}/pulls", params={"state": "all"}).json()
        assert pulls, "no PR was opened at the forge"
        assert any(p.get("merged") for p in pulls), f"no PR merged at the forge: {pulls}"

    # 4c. Git truth — the mock harness's file is present on the bare origin's main.
    tree = _git_bare(origin_bare, "ls-tree", "-r", "--name-only", "main")
    assert "LANDED.md" in tree.split(), f"landed file not reachable from bare main:\n{tree}"


def _runner_config(runner_dir: Path, workspace: Path, bin_dir: Path, hub_port: int) -> RunnerConfig:
    """A migrated runner runtime pointed at the fixture workspace and the mock harness."""
    base = init_runner_environment(runner_dir)  # scaffolds config + migrates the store
    return dataclasses.replace(
        base,
        hub_url=f"http://127.0.0.1:{hub_port}",
        workspace_root=str(workspace),
        workspace_envs=(RUNNER_ENV,),
        harness_binary=str(bin_dir / "mock-claude-code"),
        # The mock façade has no permission gate and rejects an unknown ``--permission-mode``
        # flag, so it must be omitted (``None``) — the real adapter default's own contract
        # (``bypassPermissions``, D-092): None omits the flag so the mock is unaffected.
        harness_permission_mode=None,
        base_branch="main",
    )


def _drive_until_done(
    config: RunnerConfig, hub: httpx.Client, chunk_id: str, fenced_env: dict[str, str], *, timeout: float = 120.0
) -> str:
    """Tick the reconciliation loop until the chunk is terminal; return its last status.

    Each tick is one synchronous REAP->PULL->FILL->ADVANCE pass; the spawned mock
    worker runs asynchronously, so ticks are interleaved with short waits that let it
    make its commit and exit before ADVANCE judges it.
    """
    prior = dict(os.environ)
    os.environ.update(fenced_env)  # the runner spawns the fenced mock harness in-process
    try:
        deadline = time.monotonic() + timeout
        status = "ready"
        while time.monotonic() < deadline:
            run_single_tick(config)
            detail = hub.get(f"/api/chunks/{chunk_id}")
            assert detail.status_code == 200, detail.text
            status = detail.json()["status"]
            if status in {"done", "stopped", "needs_human"}:
                return status
            time.sleep(0.5)
        return status
    finally:
        os.environ.clear()
        os.environ.update(prior)


# --------------------------------------------------------------------------- #
# Scenario: the PM item reaches the build worker through the pass-through (criterion 1)
# --------------------------------------------------------------------------- #


def _pm_graph_yaml() -> str:
    """The ``default-delivery`` shape whose build node reads its PM item through the proxy."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _PM_BUILD_SCRIPT,
                "judgement": {
                    "prompt": _JUDGEMENT_SCRIPT,
                    "choices": {"pass": {"description": "Committed and green.", "to": "review"}},
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
                        "pass": {"description": "Passes cold-eyes review.", "to": "deliver"},
                        "fail": {"description": "Blocking issues.", "to": "build"},
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {"executor": "hub", "mode": "merge-to-main"},
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


@contextlib.contextmanager
def _runner_api(config: RunnerConfig) -> Iterator[None]:
    """Serve the runner's local API in a thread — the daemon the worker's verbs POST/GET to.

    The reconciliation loop is still driven synchronously by the test (``run_single_tick``);
    this only stands up the local-API surface so the real ``blizzard runner pm-items`` verb
    has a daemon to reach. It touches no store (the pm-item route is a pure hub proxy), so it
    runs alongside the tick without contention.
    """
    app = build_hosted_app(config)
    server = uvicorn.Server(uvicorn.Config(app, host=config.host, port=config.port, log_level="warning"))
    thread = threading.Thread(target=server.run, name="runner-local-api", daemon=True)
    thread.start()
    client = httpx.Client(base_url=f"http://{config.host}:{config.port}", timeout=10.0)
    try:
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            with contextlib.suppress(httpx.HTTPError):
                if client.get("/api/health").status_code == 200:
                    break
            time.sleep(0.1)
        else:
            raise AssertionError("runner local API did not come up")
        yield
    finally:
        client.close()
        server.should_exit = True
        thread.join(timeout=10.0)


def test_build_worker_reads_pm_item_through_the_passthrough(tmp_path: Path) -> None:
    """The build worker fetches its issue body + comments through the runner->hub proxy (D-084).

    Criterion 1's pass-through half, end to end: the chunk's issue carries a distinctive
    body and comment; the build node reads them with the *real* ``blizzard runner pm-items``
    verb (the runner's local API forwarding to the hub, which reads the forge with its own
    credentials — the worker never crosses a layer), commits the fetched text, and the chunk
    lands. The exact body and comment reachable from the bare origin's ``main`` prove the
    contents travelled worker -> runner proxy -> hub -> forge and back into landed work.
    """
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
    fixture_root = scratch / FIXTURE_ENV
    workspace = fixture_root / "workspace"
    origins = fixture_root / "origins"
    origin_bare = origins / f"{REPO_NAME}.git"
    (workspace / ".blizzard-mock-harness-fence").write_text("e2e fence marker\n")

    forge_port, hub_port = _free_port(), _free_port()
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _pm_graph_yaml()}).status_code == 201

        # File an issue with a distinctive body AND a distinctive comment, then ingest it.
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "pass-through", "body": _PM_BODY})
        assert issue.status_code == 201, issue.text
        issue_number = issue.json()["number"]
        commented = forge.post(f"/repos/{REPO}/issues/{issue_number}/comments", json={"body": _PM_COMMENT})
        assert commented.status_code == 201, commented.text

        # Ingest the item's *canonical web URL* (D-075) — the pass-through parses owner/
        # repo/number from it and re-issues the read against the hub's own forge base URL,
        # so the github.com host is nominal (the sibling scenarios ingest a bare shorthand
        # only because they never exercise the fetch).
        ingested = hub.post(
            "/api/chunks",
            json={"pointers": [{"provider": "github", "url": f"https://github.com/{REPO}/issues/{issue_number}"}]},
        )
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]

        # Sanity: the hub's own pass-through returns the body + comment (the runner's proxy
        # forwards to exactly this route).
        item = hub.get(f"/api/chunks/{chunk_id}/pm-item")
        assert item.status_code == 200, item.text
        assert item.json()["body"] == _PM_BODY
        assert item.json()["comments"] == [_PM_COMMENT]

        # Drive the loop with the runner's local API up so the worker's `pm-items` verb lands.
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(config, host="127.0.0.1", port=_free_port())
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"

        with _runner_api(config):
            status = _drive_until_done(config, hub, chunk_id, fenced)

        assert status == "done", f"chunk did not reach done (last status {status!r})"

    # Git truth: the body and comment the worker fetched through the pass-through are on
    # the bare origin's main — the contents reached the worker and landed.
    landed = _git_bare(origin_bare, "show", "main:LANDED.md")
    assert _PM_BODY in landed, f"the fetched issue body did not reach the worker:\n{landed}"
    assert _PM_COMMENT in landed, f"the fetched issue comment did not reach the worker:\n{landed}"
