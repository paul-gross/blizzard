"""The acceptance loop — the `test_acceptance_loop` scenario of the standing e2e smoke.

One chunk travels ingest -> acquire -> build -> review (scripted PASS) -> deliver ->
landed, asserted at both the bare origin (git truth) and the hub's derived ``done``
status (fleet truth). Self-managed, zero-token, no-network, no in-process shortcuts.
Skipped unless ``BLIZZARD_E2E=1`` and the fixture workspace layout is discoverable."""

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

from blizzard.hub.config import HubConfig, WorkSourceConfig
from blizzard.runner.app import build_hosted_app
from blizzard.runner.config import ENV_TRANSCRIPTS_ROOT, RunnerConfig
from blizzard.runner.loop.build import LoopWiring
from blizzard.runner.runtime import init_environment as init_runner_environment
from tests.support import daemon_log_sink, read_daemon_log, write_work_sources

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e acceptance loop needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

# The fixture project repo the loop drives and the owner the forge/hub address it under
# (BZ_FORGE_OWNER; see `hub/graphs/scripts/land_default.py`).
OWNER = "blizzard"
REPO_NAME = "toy-api"
REPO = f"{OWNER}/{REPO_NAME}"
# The env keying the disposable fixture world (outer). The runner acquires its own
# INNER env (``e1``, the runner's default pool) inside the fixture workspace.
FIXTURE_ENV = "e2e"
RUNNER_ENV = "e1"

# The mock harness's fence var, forwarded to workers through the spawn-environment
# allowlist's operator-extension knob (issue #88); see MOCK_HARNESS_ENV_PASSTHROUGH.
MOCK_HARNESS_FENCE_VAR = "BLIZZARD_MOCK_HARNESS_FENCE"
# The vars every scripted mock-fleet scenario's worker child needs — mock-only names, so
# they ride the allowlist's operator-extension knob rather than the base allowlist.
MOCK_HARNESS_ENV_PASSTHROUGH = (MOCK_HARNESS_FENCE_VAR, ENV_TRANSCRIPTS_ROOT)

# The env var every scenario's ``[[work_source]]`` names as its credential —
# a dummy value suffices, since the mock forge checks no token.
WORK_SOURCE_TOKEN_ENV = "BZ_WORK_SOURCE_TOKEN_TOYAPI"

# Appended to a build prompt after a real commit: pushes the branch and declares it via
# `blizzard runner artifact commit` (issue #143); declaring twice per lease is harmless.
_PUSH_AND_DECLARE_SCRIPT = (
    "_branch = subprocess.run(\n"
    '    ["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout.strip()\n"
    "_commit = subprocess.run(\n"
    '    ["git", "-C", repo, "rev-parse", "HEAD"],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout.strip()\n"
    'subprocess.run(["git", "-C", repo, "push", "origin", _branch], check=True)\n'
    "subprocess.run(\n"
    '    ["blizzard", "runner", "artifact", "commit",\n'
    '     "--repo", repo, "--branch", _branch, "--commit", _commit],\n'
    "    check=True,\n"
    ")\n"
)

# The scripted build-node prompt: the prompt is the program, run under the mock harness
# with the acquired env dir as cwd, so it targets `toy-api` by relative path.
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
    ")\n" + _PUSH_AND_DECLARE_SCRIPT
)
# The judgement-resume prompt: also arrives as code.
_JUDGEMENT_SCRIPT = "verdict('pass', 'the mock harness committed the change; checks are green')\n"

# The review node scripted to PASS on first look, so the build commit travels straight to
# deliver with no re-build; the base turn is a no-op, the verdict comes on judgement resume.
_REVIEW_SCRIPT = "pass\n"
_REVIEW_JUDGEMENT = "verdict('pass', 'cold-eyes review: the committed change is clean; ready to deliver')\n"

# The pass-through scenario's distinctive work item — a body + a comment whose exact text
# is asserted on the bare origin's main.
_WORK_ITEM_BODY = "PASSTHROUGH-BODY: the widget flake reproduces under load"
_WORK_ITEM_COMMENT = "PASSTHROUGH-COMMENT: attached a failing repro in the linked gist"

# Build turn: reads the chunk's work item via the real `blizzard runner work-items` verb,
# then commits the fetched body + comment so the pass-through's output lands as git truth.
_WORK_ITEM_BUILD_SCRIPT = (
    "import os, json, subprocess, pathlib\n"
    f"repo = {REPO_NAME!r}\n"
    "chunk_id = os.environ['BLIZZARD_CHUNK_ID']\n"
    "out = subprocess.run(\n"
    '    ["blizzard", "runner", "work-items", chunk_id],\n'
    "    check=True, capture_output=True, text=True,\n"
    ").stdout\n"
    "item = json.loads(out)['items'][0]\n"
    "payload = item['body'] + '\\n' + '\\n'.join(item['comments']) + '\\n'\n"
    '(pathlib.Path(repo) / "LANDED.md").write_text(payload)\n'
    'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
    "subprocess.run(\n"
    '    ["git", "-C", repo,\n'
    '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
    '     "commit", "-m", "feat: land the work item fetched through the pass-through"],\n'
    "    check=True,\n"
    ")\n" + _PUSH_AND_DECLARE_SCRIPT
)


def _graph_yaml() -> str:
    """The scripted ``default-delivery`` graph — ``build -> review -> deliver``.

    Named ``default-delivery`` so the hub's lazy default-graph mint reuses this
    pre-minted graph by name — the packaged prompts are LLM prose the mock cannot ``exec``.
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
            "deliver": {
                "executor": "hub",
                "run": [{"command": "python3 -m blizzard.hub.graphs.scripts.land_default"}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Landed.", "to": "done"},
                        "conflict": {"description": "Conflict.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


# --------------------------------------------------------------------------- #
# Workspace-layout discovery (the sibling blizzard-mock worktree + winter source)


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


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _git_bare(bare: Path, *args: str) -> str:
    return subprocess.run(["git", "--git-dir", str(bare), *args], check=True, capture_output=True, text=True).stdout


def _await_http(
    proc: subprocess.Popen[str], client: httpx.Client, path: str, *, log: Path | None = None, timeout: float = 40.0
) -> None:
    """Block until ``path`` answers 200, or fail naming why.

    ``log`` is the daemon's own log file (issue #145); the early-exit diagnostic reads it
    rather than a drained pipe, and always names the exit code too.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"process exited early ({proc.returncode}):\n{read_daemon_log(log)}")
        with contextlib.suppress(httpx.HTTPError):
            if client.get(path).status_code == 200:
                return
        time.sleep(0.15)
    raise AssertionError(f"process did not answer {path} within {timeout}s")


@contextlib.contextmanager
def _forge(bin_dir: Path, origins: Path, port: int) -> Iterator[httpx.Client]:
    log = origins.parent / "forge.log"
    proc = subprocess.Popen(
        [str(bin_dir / "blizzard-mock-forge"), "--repos-dir", str(origins), "--host", "127.0.0.1", "--port", str(port)],
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15.0)
    try:
        _await_http(proc, client, "/healthz", log=log)
        yield client
    finally:
        client.close()
        _terminate(proc)


@contextlib.contextmanager
def _hub(
    hub_dir: Path,
    forge_port: int,
    port: int,
    *,
    route_token_mode: str | None = None,
    produces_mode: str | None = None,
    annotate: bool = False,
    annotation_interval_seconds: int | None = None,
) -> Iterator[httpx.Client]:
    env = {
        **os.environ,
        "BZ_FORGE_URL": f"http://127.0.0.1:{forge_port}",
        "BZ_FORGE_OWNER": OWNER,
        WORK_SOURCE_TOKEN_ENV: "e2e-fixture-token",
    }
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    subprocess.run([hub_bin, "init", str(hub_dir)], check=True, capture_output=True, text=True)
    # Declare the one work source every scenario ingests against; `annotate` opts it into
    # the forge-status label sweep (issue #179).
    write_work_sources(
        hub_dir,
        [
            WorkSourceConfig(
                name=REPO_NAME,
                provider="github",
                repo=REPO,
                token_env=WORK_SOURCE_TOKEN_ENV,
                api_base=f"http://127.0.0.1:{forge_port}",
                annotate=annotate,
            )
        ],
    )
    if route_token_mode is not None or produces_mode is not None or annotation_interval_seconds is not None:
        # issue #84b / #113 / #179 — flags read once, at `host` startup: set before the
        # daemon starts, not mutable afterward.
        config = HubConfig.load(hub_dir)
        overrides: dict[str, object] = {}
        if route_token_mode is not None:
            overrides["route_token_mode"] = route_token_mode
        if produces_mode is not None:
            overrides["produces_mode"] = produces_mode
        if annotation_interval_seconds is not None:
            overrides["annotation_interval_seconds"] = annotation_interval_seconds
        config = dataclasses.replace(config, **overrides)
        config.config_path.write_text(config.to_toml())
    log = hub_dir / "daemon.log"
    proc = subprocess.Popen(
        [hub_bin, "host", "--dir", str(hub_dir), "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=daemon_log_sink(log),
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30.0)
    try:
        _await_http(proc, client, "/api/health", log=log)
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


def test_acceptance_loop_one_chunk_ingest_to_landed(tmp_path: Path) -> None:
    """One chunk travels the whole lifecycle and derives ``done``."""
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

    # Fence the fixture tree so the mock harness will run (gated on this marker file + env
    # var); it covers every acquired env worktree under it via the engine's ancestor walk.
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
            json={"tokens": [f"{REPO_NAME}:{issue_number}"]},
        )
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "not_ready"  # rests not-ready
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
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
    """A migrated runner runtime pointed at the fixture workspace and the mock harness.

    ``host``/``port`` bind to a free port rather than the base config's default, which
    can collide with this machine's live dogfood runner (issue #143, Phase 4;
    see ``AGENTS.local.md``)."""
    base = init_runner_environment(runner_dir)  # scaffolds config + migrates the store
    return dataclasses.replace(
        base,
        host="127.0.0.1",
        port=_free_port(),
        hub_url=f"http://127.0.0.1:{hub_port}",
        workspace_root=str(workspace),
        workspace_envs=(RUNNER_ENV,),
        harness_binary=str(bin_dir / "mock-claude-code"),
        # The mock façade rejects an unknown ``--permission-mode`` flag, so it must be
        # omitted (``None``).
        harness_permission_mode=None,
        # A path that is never created, so the external-usage sampler's missing-credentials
        # soft failure trips before any request is built (issue #218).
        external_usage_credentials_path=str(runner_dir / "no-such-credentials.json"),
        base_branch="main",
        worker_env_passthrough=MOCK_HARNESS_ENV_PASSTHROUGH,
    )


def _drive_until_done(
    config: RunnerConfig, hub: httpx.Client, chunk_id: str, fenced_env: dict[str, str], *, timeout: float = 120.0
) -> str:
    """Tick the reconciliation loop until the chunk is terminal; return its last status.

    Each tick is one synchronous REAP->PULL->FILL->ADVANCE pass, interleaved with short
    waits so the asynchronously spawned mock worker can commit before ADVANCE judges it.
    """
    prior = dict(os.environ)
    os.environ.update(fenced_env)  # the runner spawns the fenced mock harness in-process
    try:
        with _runner_api(config):
            deadline = time.monotonic() + timeout
            status = "ready"
            while time.monotonic() < deadline:
                LoopWiring.of(config).tick_once()
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
# Scenario: the work item reaches the build worker through the pass-through (criterion 1)


def _work_item_graph_yaml() -> str:
    """The ``default-delivery`` shape whose build node reads its work item through the proxy."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": _WORK_ITEM_BUILD_SCRIPT,
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
            "deliver": {
                "executor": "hub",
                "run": [{"command": "python3 -m blizzard.hub.graphs.scripts.land_default"}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Landed.", "to": "done"},
                        "conflict": {"description": "Conflict.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


@contextlib.contextmanager
def _runner_api(config: RunnerConfig) -> Iterator[None]:
    """Serve the runner's local API in a thread — the daemon the worker's verbs POST/GET to.

    Touches no store, so it runs alongside the synchronously driven reconciliation tick
    without contention.
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


def test_build_worker_reads_work_item_through_the_passthrough(tmp_path: Path) -> None:
    """The build worker fetches its issue body + comments through the runner->hub proxy
    and commits the fetched text; asserted reachable from the bare origin's ``main``."""
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
        assert hub.post("/api/graphs", json={"definition_yaml": _work_item_graph_yaml()}).status_code == 201

        # File an issue with a distinctive body AND a distinctive comment, then ingest it.
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "pass-through", "body": _WORK_ITEM_BODY})
        assert issue.status_code == 201, issue.text
        issue_number = issue.json()["number"]
        commented = forge.post(f"/repos/{REPO}/issues/{issue_number}/comments", json={"body": _WORK_ITEM_COMMENT})
        assert commented.status_code == 201, commented.text

        # Ingest by {source, ref} — the source names the configured binding, the ref is
        # its opaque item token (the issue number).
        ingested = hub.post(
            "/api/chunks",
            json={"tokens": [f"{REPO_NAME}:{issue_number}"]},
        )
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202  # ready for the runner

        # Sanity: the hub's own pass-through returns the body + comment, one entry per pointer.
        item = hub.get(f"/api/chunks/{chunk_id}/work-items")
        assert item.status_code == 200, item.text
        entry = item.json()["items"][0]
        assert entry["body"] == _WORK_ITEM_BODY
        assert entry["comments"] == [_WORK_ITEM_COMMENT]

        # Drive the loop — `_runner_config` binds a free host/port and `_drive_until_done`
        # wraps it in `_runner_api`, so the worker's CLI verbs have a daemon to reach.
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        fenced = dict(os.environ)
        fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"

        status = _drive_until_done(config, hub, chunk_id, fenced)

        assert status == "done", f"chunk did not reach done (last status {status!r})"

    # Git truth: the body and comment the worker fetched are on the bare origin's main.
    landed = _git_bare(origin_bare, "show", "main:LANDED.md")
    assert _WORK_ITEM_BODY in landed, f"the fetched issue body did not reach the worker:\n{landed}"
    assert _WORK_ITEM_COMMENT in landed, f"the fetched issue comment did not reach the worker:\n{landed}"
