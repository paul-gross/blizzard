"""The acceptance loop — the standing e2e smoke test (verification.md, P6 exit).

ONE chunk travels the whole lifecycle — ingest -> acquire -> mock-scripted commit ->
deliver -> landed in the bare origin — and the assertion holds at **both ends**: the
commit is reachable from the bare origin's ``main`` (git truth) *and* the hub's facts
derive the chunk ``done`` (fleet truth). This is the P6 exit criterion of
``blizzard-discovery:/implementation/verification.md`` turned into a committed,
repeatable artifact.

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
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

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


def _graph_yaml() -> str:
    """The minimal ``default-delivery`` graph, scripted so the mock harness can run it.

    Named ``default-delivery`` so the hub's lazy ``ensure_default`` (POST /chunks)
    reuses this pre-minted graph by name (D-081) instead of minting the packaged
    prose graph — the packaged prompts are LLM prose the mock cannot ``exec``.
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
                            "to": "deliver",
                        }
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
