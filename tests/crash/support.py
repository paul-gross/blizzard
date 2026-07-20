"""Scaffolding for the kill-9 sweep (``blizzard:crash-sweep``).

The sweep runs the daemons as **real subprocesses** so a crash point can SIGKILL a
whole process the way ``kill -9`` would — the one thing the in-process component tier
cannot do. It reuses the e2e stack (mock forge + mock harness + fixture workspace +
real hub/runner), but drives the runner as a hosted daemon (``blizzard runner host``)
rather than in-process ticks, and arms a registry crash point via the environment.

The daemons converge on their own once restarted unarmed: the runner ticks on a fast
interval and its startup pass is REAP; the hub re-applies a re-flushed completion
idempotently. The sweep asserts the invariant checker after the crash and again after
convergence, plus exactly-once delivery.
"""

from __future__ import annotations

import contextlib
import dataclasses
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx

from blizzard.hub.config import PmSourceConfig
from blizzard.runner.config import RunnerConfig
from blizzard.runner.runtime import init_environment as init_runner_environment
from tests.support import write_pm_sources

OWNER = "blizzard"
REPO_NAME = "toy-api"
REPO = f"{OWNER}/{REPO_NAME}"
FIXTURE_ENV = "crash"
RUNNER_ENV = "e1"

# A brisk tick so a scenario converges in seconds, not the daemon's 30s production cadence.
TICK_SECONDS = "0.3"

# The env var every scenario's ``[[pm_source]]`` names as its credential —
# shared across every source this support module declares, since the mock forge checks
# no token: one env var suffices regardless of how many sources are configured.
PM_TOKEN_ENV = "BZ_PM_TOKEN_CRASH"


def default_pm_sources(forge_port: int) -> tuple[PmSourceConfig, ...]:
    """The one source the crash sweep's ``build -> deliver`` scenarios ingest against."""
    return (
        PmSourceConfig(
            name=REPO_NAME,
            provider="github",
            repo=REPO,
            token_env=PM_TOKEN_ENV,
            api_base=f"http://127.0.0.1:{forge_port}",
        ),
    )


# Env var names the crash mechanism and the mock-harness fence read.
ENV_CRASH_POINT = "BLIZZARD_CRASH_POINT"
ENV_CRASH_FENCE = "BLIZZARD_CRASH_FENCE"
ENV_HARNESS_FENCE = "BLIZZARD_MOCK_HARNESS_FENCE"


@dataclass(frozen=True)
class CrashEnv:
    """The session-shared fixture world the sweep runs every point against."""

    bin_dir: Path
    workspace: Path
    origins: Path
    forge_port: int
    forge: httpx.Client


# --------------------------------------------------------------------------- #
# Workspace-layout discovery (mirrors tests/e2e/test_acceptance_loop.py)
# --------------------------------------------------------------------------- #


def blizzard_root() -> Path:
    return Path(__file__).resolve().parents[2]


def mock_bin_dir() -> Path | None:
    mock = blizzard_root().parent / "blizzard-mock"
    bin_dir = mock / ".venv" / "bin"
    if (bin_dir / "blizzard-mock-fixture").is_file() and (bin_dir / "mock-claude-code").is_file():
        return bin_dir
    return None


def winter_source() -> Path | None:
    explicit = os.environ.get("BLIZZARD_MOCK_WINTER_SOURCE")
    start = Path(explicit).resolve() if explicit else blizzard_root()
    for directory in [start, *start.parents]:
        if (directory / ".winter" / "config.toml").is_file() and (directory / "tools" / "winter-cli").is_dir():
            return directory
    return None


# --------------------------------------------------------------------------- #
# The build → deliver sweep graph (prompt-is-the-program)
# --------------------------------------------------------------------------- #


def build_script(landed_file: str) -> str:
    """A scripted build node that makes a real commit adding ``landed_file``."""
    return (
        "import subprocess, pathlib\n"
        f"repo = {REPO_NAME!r}\n"
        f"(pathlib.Path(repo) / {landed_file!r}).write_text('landed by the crash sweep\\n')\n"
        'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
        "subprocess.run(\n"
        '    ["git", "-C", repo,\n'
        '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
        '     "commit", "-m", "feat: land a change from the crash sweep"],\n'
        "    check=True,\n"
        ")\n"
    )


_JUDGEMENT_SCRIPT = "verdict('pass', 'the mock harness committed the change; checks are green')\n"

# The migrate scenario's source-graph judgement (#90): the build node hands the chunk to
# the `triage-delivery` graph instead of delivering in place. Its build prompt is a no-op
# (`pass`) so the source node commits nothing — the real landing commit is made by the
# TARGET graph's own `build` node after the migration re-queues the chunk there, keeping
# the sweep's exactly-once-on-`main` assertion honest (no source branch to double-merge).
_MIGRATE_JUDGEMENT_SCRIPT = "verdict('migrate', 'hand the chunk to the triage-delivery graph')\n"


# The generic sweep's ``deliver`` node command — a real merge-to-main, not a ``true``
# no-op. The runner pushes each build commit to a feature branch; this step opens a PR
# per submitted branch and merges it to the base by pinned SHA against the mock forge, so
# the change actually LANDS on bare ``main`` and the sweep's exactly-once-on-``main``
# assertion is meaningful (before #67 the ``deliver`` node was the coordinator's own
# ``mode: merge-to-main`` — a bare ``true`` after the retirement never merged anything and
# left every "landed once on bare main" assertion asserting against an unmoved ``main``).
# Driven entirely off the injected env (``BZ_FORGE_URL`` / ``BZ_HUB_GIT_COMMITS`` /
# ``BZ_HUB_BASE_BRANCH``), never a typed forge seam (policy-in-YAML, #67); idempotent by
# construction — re-merging an already-merged head is a git "Already up to date" no-op, so
# a crash-recovery re-run lands nothing twice. It prints a non-choice line, so the
# executor's outcome mapping falls through to the node's default ``success`` edge.
LAND_STEP = """python3 - <<'PYEOF'
import json, os, urllib.error, urllib.request

forge = os.environ["BZ_FORGE_URL"]
base = os.environ.get("BZ_HUB_BASE_BRANCH", "main")
commits = json.loads(os.environ.get("BZ_HUB_GIT_COMMITS") or "[]")


def call(method, path, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        forge + path, data=data, headers={"Content-Type": "application/json"}, method=method
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as exc:
        return exc.code, None


for c in commits:
    repo = c["repo"] if "/" in c["repo"] else "blizzard/" + c["repo"]
    status, body = call(
        "POST",
        "/repos/%s/pulls" % repo,
        {"title": "land", "head": c["branch"], "base": base, "body": "", "user": "blizzard-hub"},
    )
    if status == 201 and body:
        call(
            "PUT",
            "/repos/%s/pulls/%s/merge" % (repo, body["number"]),
            {"commit_message": "blizzard: land", "sha": c["commit"], "merge_method": "merge", "user": "blizzard-hub"},
        )
print("landed the submitted branches")
PYEOF
"""


def graph_yaml(landed_file: str) -> str:
    """A minimal ``build -> deliver`` graph, named ``default-delivery`` so ingest reuses it.

    Shorter than the packaged build→review→deliver shape — every GENERIC crash point
    (reap, pull, fill, spawn, advance, flush) is still traversed; ``deliver`` is a generic
    hub command node (#67) whose ``run:`` step (:data:`LAND_STEP`) actually merges every
    submitted branch to bare ``main`` against the mock forge, so the sweep's
    exactly-once-on-``main`` assertion is real. Each scenario lands a **unique** file so
    successive points never collide in the shared origins.
    """
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": build_script(landed_file),
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
            "deliver": {
                "executor": "hub",
                "run": [{"command": LAND_STEP}],
                "judgement": {
                    "choices": {
                        "success": {"description": "Delivered.", "to": "done"},
                        "failure": {"description": "Failed to deliver.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


#: The nudge scenario's unattached `produces:` name (issue #113, Phase 4) — the build
#: node declares it but the mock worker's judgement script never attaches it (real
#: harnesses would; scripting a conditional attach-on-nudge reply is not what the
#: `nudge.*` crash points need proven — the "at most one nudge" property they guard
#: holds regardless of whether the worker ever complies).
NUDGE_PRODUCES_NAME = "finding"


def nudge_graph_yaml(landed_file: str) -> str:
    """:func:`graph_yaml`'s ``build -> deliver`` shape, plus one unattached
    ``produces:`` name on ``build`` (issue #113, Phase 4) — the condition
    :func:`~blizzard.runner.loop.steps._advance_exited_worker`'s nudge-once checks
    before ever eliciting it: no pushed git commit is named :data:`NUDGE_PRODUCES_NAME`
    and the mock worker never attaches it, so every pass through ``build`` opens the
    `nudge.*` windows the dedicated scenario arms.

    Named ``default-delivery`` like :func:`graph_yaml` — not a distinct name — because
    ingest (``POST /api/chunks`` with no explicit ``graph_id``) resolves to the newest
    *enabled* graph of the hub's configured default name via ``ensure_default``
    (``hub/api/chunks.py``), minting the packaged ``default.yaml`` shape under that name
    if none enabled yet exists. A differently-named custom graph is simply never picked
    up by ingest; sharing the name is what makes this scenario's mint the one ingest
    resolves to, exactly as :func:`graph_yaml`'s own docstring already relies on."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": build_script(landed_file),
                "produces": [NUDGE_PRODUCES_NAME],
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
            "deliver": {
                "executor": "hub",
                "run": [{"command": LAND_STEP}],
                "judgement": {
                    "choices": {
                        "success": {"description": "Delivered.", "to": "done"},
                        "failure": {"description": "Failed to deliver.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def migrate_source_yaml() -> str:
    """A source graph (`default-delivery`, so ingest pins it) whose `build` node migrates
    the chunk to the `triage-delivery` graph (#90) rather than delivering in place.

    The build prompt is a no-op: the migration re-queues the chunk at the target graph's
    own `build` node (name-match-else-entry), which does the real commit + deliver. So the
    only landing branch is the target's — a source branch that could double-merge never
    exists, keeping the exactly-once-on-`main` assertion meaningful."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": "pass\n",
                "judgement": {
                    "prompt": _MIGRATE_JUDGEMENT_SCRIPT,
                    "choices": {
                        "migrate": {"description": "Hand off to triage-delivery.", "to": "graph:triage-delivery"},
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def migrate_target_yaml(landed_file: str) -> str:
    """The migration target (`triage-delivery`, #90) — a standard `build -> deliver` graph
    whose `build` node name-matches the source's, so the migration lands there and the
    chunk runs to `done` under the new graph."""
    return graph_yaml(landed_file).replace("name: default-delivery", "name: triage-delivery", 1)


def migrate_hub_source_yaml() -> str:
    """A source graph (`default-delivery`, so ingest pins it) whose `build` migrates to the
    hub-landing target `triage-hub` (issue #111) rather than the runner-landing
    `triage-delivery` :func:`migrate_source_yaml` uses. Same no-op build prompt: the source
    node commits nothing, so the target's landing hub node has no branches to merge (the
    exactly-once assertion is on convergence, not a landed file — see the test)."""
    return migrate_source_yaml().replace("graph:triage-delivery", "graph:triage-hub", 1)


def migrate_hub_target_yaml() -> str:
    """The hub-landing migration target (`triage-hub`, issue #111): its **entry** node
    `build` — which name-matches the source's migrating `build`, so the migration lands
    there — is **hub-executed**, not a runner node. A migration re-pinning a chunk onto a
    hub node is retained by the hub exactly as a transition into one is: it derives
    `delivering` (never runner-claimable `ready`) and the landed hub node is driven by the
    holding runner's ADVANCE poll to `done`. The hub node reuses :data:`LAND_STEP`, which
    with no submitted branches (the source committed nothing) is a clean no-op that prints
    its success line and routes the default `success` edge to `done`."""
    import yaml

    graph = {
        "name": "triage-hub",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "hub",
                "run": [{"command": LAND_STEP}],
                "judgement": {
                    "choices": {
                        "success": {"description": "Delivered.", "to": "done"},
                        "failure": {"description": "Failed to deliver.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def intended_migrate_source_yaml() -> str:
    """A plain single-graph source (`default-delivery`, so ingest pins it) for the
    **intended**-migration crash scenario (issue #124) — unlike :func:`migrate_source_yaml`,
    no edge here is a `graph:<name>` cross-graph edge at all; this is an entirely ordinary
    graph. The migration is driven out of band: a claimed chunk's PATCHed
    `intended_migration` (set before the runner ever claims it — the window is open at
    `ready` too) is consulted at the ordinary `build -pass-> deliver` transition below.
    `deliver` is a dummy hub node, never actually reached: the scenario arms a `forced`
    intent naming the migration target's own `build` node, which fires unconditionally
    regardless of this transition's own destination name."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": "pass\n",
                "judgement": {
                    "prompt": _JUDGEMENT_SCRIPT,
                    "choices": {
                        "pass": {"description": "Ready.", "to": "deliver"},
                    },
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "run": [{"command": "true"}],
                "judgement": {
                    "choices": {
                        "success": {"description": "Delivered.", "to": "done"},
                        "failure": {"description": "Failed to deliver.", "to": "build"},
                    }
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


# --------------------------------------------------------------------------- #
# Process helpers
# --------------------------------------------------------------------------- #


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def git_bare(bare: Path, *args: str) -> str:
    return subprocess.run(["git", "--git-dir", str(bare), *args], check=True, capture_output=True, text=True).stdout


def await_http(
    client: httpx.Client, path: str, *, proc: subprocess.Popen[str] | None = None, timeout: float = 40.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            raise AssertionError(f"process exited early ({proc.returncode}) before answering {path}")
        with contextlib.suppress(httpx.HTTPError):
            if client.get(path).status_code == 200:
                return
        time.sleep(0.1)
    raise AssertionError(f"process did not answer {path} within {timeout}s")


def terminate(proc: subprocess.Popen[str] | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=10)
    if proc.poll() is None:
        proc.kill()


def wait_death(proc: subprocess.Popen[str], *, timeout: float = 60.0) -> int:
    """Block until the process dies; return its exit code. -9 is the SIGKILL self-crash."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        code = proc.poll()
        if code is not None:
            return code
        time.sleep(0.05)
    raise AssertionError("armed daemon did not reach its crash point within the timeout")


# --------------------------------------------------------------------------- #
# The forge (session) and the two daemons (per point)
# --------------------------------------------------------------------------- #


@contextlib.contextmanager
def forge_daemon(bin_dir: Path, origins: Path, port: int) -> Iterator[httpx.Client]:
    proc = subprocess.Popen(
        [str(bin_dir / "blizzard-mock-forge"), "--repos-dir", str(origins), "--host", "127.0.0.1", "--port", str(port)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15.0)
    try:
        await_http(client, "/healthz", proc=proc)
        yield client
    finally:
        client.close()
        terminate(proc)


def start_hub(
    hub_dir: Path,
    *,
    forge_port: int,
    port: int,
    crash_point: str | None,
    pm_sources: Sequence[PmSourceConfig] | None = None,
    new_session: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    """Start (or restart) the hub daemon; arm ``crash_point`` when it is a deliver point.

    ``pm_sources`` is declared only on the first call for ``hub_dir`` — the
    one that also runs ``hub init`` — since a restart reuses the config file already on
    disk; defaults to :func:`default_pm_sources`, the crash sweep's single source. Every
    restart still carries ``PM_TOKEN_ENV`` regardless, since the config always names it.

    ``new_session`` starts the hub as a session/process-group leader so a caller can
    ``os.killpg`` the WHOLE tree — the hub plus any ``run:`` subprocess it has spawned —
    which is what a faithful ``kill -9`` mid-script needs (a bare kill of the hub pid
    would orphan a running land script; see the #67 mid-script sweep). ``extra_env``
    layers additional variables onto the hub's environment (e.g. a land script's
    test-only pause), applied after the base env so a caller can override nothing
    load-bearing."""
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    if not (hub_dir / "blizzard-hub.toml").exists():
        subprocess.run([hub_bin, "init", str(hub_dir)], check=True, capture_output=True, text=True)
        write_pm_sources(hub_dir, pm_sources if pm_sources is not None else default_pm_sources(forge_port))
    env = {
        **os.environ,
        "BZ_FORGE_URL": f"http://127.0.0.1:{forge_port}",
        "BZ_FORGE_OWNER": OWNER,
        PM_TOKEN_ENV: "crash-fixture-token",
    }
    _apply_crash_env(env, crash_point)
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [hub_bin, "host", "--dir", str(hub_dir), "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=new_session,
    )


def write_runner_config(runner_dir: Path, *, workspace: Path, bin_dir: Path, hub_port: int, port: int) -> RunnerConfig:
    """Scaffold + persist a runner config pointed at the fixture workspace and mock harness."""
    base = init_runner_environment(runner_dir)
    config = dataclasses.replace(
        base,
        host="127.0.0.1",
        port=port,
        hub_url=f"http://127.0.0.1:{hub_port}",
        workspace_root=str(workspace),
        workspace_envs=(RUNNER_ENV,),
        harness_binary=str(bin_dir / "mock-claude-code"),
        # The mock façade has no permission gate and rejects an unknown ``--permission-mode``
        # flag, so it must be omitted here (``None``) — the contract of the real adapter's
        # default (``bypassPermissions``): None omits the flag so the mock is unaffected.
        harness_permission_mode=None,
        base_branch="main",
        # `start_runner` sets `ENV_HARNESS_FENCE` in the daemon subprocess's own env; the
        # adapter's spawn-environment allowlist (issue #88) only forwards it to a worker
        # because it is declared here, mirroring the real fleet's `[worker] env_passthrough`.
        worker_env_passthrough=(ENV_HARNESS_FENCE,),
    )
    config.config_path.write_text(config.to_toml())
    return config


def start_runner(runner_dir: Path, *, crash_point: str | None) -> subprocess.Popen[str]:
    """Start (or restart) the runner daemon; arm ``crash_point`` for a runner-side point."""
    runner_bin = str(Path(sys.executable).parent / "blizzard-runner")
    env = {**os.environ, "BZ_RUNNER_TICK_SECONDS": TICK_SECONDS, ENV_HARNESS_FENCE: "1"}
    _apply_crash_env(env, crash_point)
    return subprocess.Popen(
        [runner_bin, "host", "--dir", str(runner_dir)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def _apply_crash_env(env: dict[str, str], crash_point: str | None) -> None:
    if crash_point is not None:
        env[ENV_CRASH_FENCE] = "1"
        env[ENV_CRASH_POINT] = crash_point
    else:
        env.pop(ENV_CRASH_POINT, None)


def wait_status(client: httpx.Client, chunk_id: str, targets: set[str], *, timeout: float = 90.0) -> str:
    """Poll the hub for the chunk's derived status until it is one of ``targets``."""
    deadline = time.monotonic() + timeout
    status = "unknown"
    while time.monotonic() < deadline:
        with contextlib.suppress(httpx.HTTPError):
            resp = client.get(f"/api/chunks/{chunk_id}")
            if resp.status_code == 200:
                status = resp.json()["status"]
                if status in targets:
                    return status
        time.sleep(0.25)
    return status
