"""Scaffolding for the kill-9 sweep (``blizzard:crash-sweep``).

The sweep runs the daemons as **real subprocesses** so a crash point can SIGKILL a whole
process the way ``kill -9`` would. It reuses the e2e stack (mock forge + mock harness +
fixture workspace + real hub/runner) and arms a registry crash point via the environment.
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

from blizzard.hub.config import WorkSourceConfig
from blizzard.runner.config import RunnerConfig
from blizzard.runner.runtime import init_environment as init_runner_environment
from tests.support import daemon_log_sink, write_work_sources

OWNER = "blizzard"
REPO_NAME = "toy-api"
REPO = f"{OWNER}/{REPO_NAME}"
FIXTURE_ENV = "crash"
RUNNER_ENV = "e1"

# A brisk tick so a scenario converges in seconds, not the daemon's 30s production cadence.
TICK_SECONDS = "0.3"

# The env var every scenario's ``[[work_source]]`` names as its credential — one suffices
# for every source this module declares, since the mock forge checks no token.
WORK_SOURCE_TOKEN_ENV = "BZ_WORK_SOURCE_TOKEN_CRASH"


def default_work_sources(forge_port: int) -> tuple[WorkSourceConfig, ...]:
    """The one source the crash sweep's ``build -> deliver`` scenarios ingest against."""
    return (
        WorkSourceConfig(
            name=REPO_NAME,
            provider="github",
            repo=REPO,
            token_env=WORK_SOURCE_TOKEN_ENV,
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


# Workspace-layout discovery


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


# The build → deliver sweep graph (prompt-is-the-program)


def build_script(landed_file: str) -> str:
    """A scripted build node that makes a real commit adding ``landed_file``, then pushes
    its branch and declares it through the real `blizzard runner artifact commit` verb
    (issue #143, Phase 4)."""
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
        "_branch = subprocess.run(\n"
        '    ["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],\n'
        "    check=True, capture_output=True, text=True,\n"
        ").stdout.strip()\n"
        "_commit = subprocess.run(\n"
        '    ["git", "-C", repo, "rev-parse", "HEAD"],\n'
        "    check=True, capture_output=True, text=True,\n"
        ").stdout.strip()\n"
        'subprocess.run(["git", "-C", repo, "push", "--force-with-lease", "origin", _branch], check=True)\n'
        "subprocess.run(\n"
        '    ["blizzard", "runner", "artifact", "commit",\n'
        '     "--repo", repo, "--branch", _branch, "--commit", _commit],\n'
        "    check=True,\n"
        ")\n"
    )


def pre_declare_build_script(landed_file: str, pushed_marker: Path, go_marker: Path) -> str:
    """:func:`build_script`'s commit + push, then an in-test fence (``bzh:crash-sweep`` D2):
    write ``pushed_marker`` once pushed, then block on ``go_marker`` before declaring — pinning
    the pre-declaration window deterministically. The commit is idempotent (only if dirty): a
    retried attempt reuses the same workdir, which already carries the first attempt's commit,
    and a plain ``git commit`` there would find nothing to commit and raise."""
    return (
        "import subprocess, pathlib, time\n"
        f"repo = {REPO_NAME!r}\n"
        f"(pathlib.Path(repo) / {landed_file!r}).write_text('landed by the crash sweep\\n')\n"
        'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
        "_dirty = subprocess.run(\n"
        '    ["git", "-C", repo, "status", "--porcelain"],\n'
        "    check=True, capture_output=True, text=True,\n"
        ").stdout.strip()\n"
        "if _dirty:\n"
        "    subprocess.run(\n"
        '        ["git", "-C", repo,\n'
        '         "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
        '         "commit", "-m", "feat: land a change from the crash sweep"],\n'
        "        check=True,\n"
        "    )\n"
        "_branch = subprocess.run(\n"
        '    ["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"],\n'
        "    check=True, capture_output=True, text=True,\n"
        ").stdout.strip()\n"
        "_commit = subprocess.run(\n"
        '    ["git", "-C", repo, "rev-parse", "HEAD"],\n'
        "    check=True, capture_output=True, text=True,\n"
        ").stdout.strip()\n"
        'subprocess.run(["git", "-C", repo, "push", "--force-with-lease", "origin", _branch], check=True)\n'
        f"pathlib.Path({str(pushed_marker)!r}).write_text('pushed\\n')\n"
        f"_go = pathlib.Path({str(go_marker)!r})\n"
        "while not _go.exists():\n"
        "    time.sleep(0.05)\n"
        "subprocess.run(\n"
        '    ["blizzard", "runner", "artifact", "commit",\n'
        '     "--repo", repo, "--branch", _branch, "--commit", _commit],\n'
        "    check=True,\n"
        ")\n"
    )


_JUDGEMENT_SCRIPT = "verdict('pass', 'the mock harness committed the change; checks are green')\n"

#: The ``git_commit`` ``produces:`` every genuinely-committing build node declares (D1,
#: ``bzh:crash-sweep``) — arms ``LAND_STEP``'s empty-delivery refusal via ``Graph.declares_git_commit``.
_GIT_COMMIT_PRODUCES = [{"name": "commit", "kind": "git_commit"}]

# The migrate scenario's source-graph judgement (#90): the build node hands the chunk to
# the `triage-delivery` graph instead of delivering in place.
_MIGRATE_JUDGEMENT_SCRIPT = "verdict('migrate', 'hand the chunk to the triage-delivery graph')\n"


# Merges each submitted branch to base by pinned SHA against the mock forge; idempotent by
# construction, and refuses an empty delivery like production's ``LandRun.pending()`` (D1).
LAND_STEP = """python3 - <<'PYEOF'
import json, os, sys, urllib.error, urllib.request

forge = os.environ["BZ_FORGE_URL"]
base = os.environ.get("BZ_HUB_BASE_BRANCH", "main")
commits = json.loads(os.environ.get("BZ_HUB_GIT_COMMITS") or "[]")

if not commits and os.environ.get("BZ_HUB_EXPECT_GIT_COMMITS", "1") != "0":
    print(
        "no git commits to deliver: this chunk submitted no git_commit artifact, so there "
        "is nothing to open a PR for.",
        file=sys.stderr,
    )
    sys.exit(1)


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

    Every GENERIC crash point (reap, pull, fill, spawn, advance, flush) is traversed; each
    scenario lands a **unique** file so successive points never collide in the shared origins.
    """
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": build_script(landed_file),
                "produces": _GIT_COMMIT_PRODUCES,
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


def checks_graph_yaml(landed_file: str) -> str:
    """:func:`graph_yaml`'s ``build -> deliver`` shape, plus a real ``checks:`` on ``build``
    (issue #114) — what opens the `checks.*` crash windows the dedicated scenario arms.

    The check is ``true``: a green check that names no toolchain and runs in any env. Named
    ``default-delivery`` like :func:`graph_yaml` so ingest resolves to it."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": build_script(landed_file),
                "produces": _GIT_COMMIT_PRODUCES,
                "checks": ["true"],
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


#: The nudge scenario's unattached `produces:` name (issue #113) — declared by `build`,
#: never attached, keeping the `nudge.*` windows open every pass.
NUDGE_PRODUCES_NAME = "finding"


def nudge_graph_yaml(landed_file: str) -> str:
    """:func:`graph_yaml`'s shape, plus one unattached ``produces:`` name on ``build``
    (issue #113): declared but never attached, so every pass opens the `nudge.*` windows
    the dedicated scenario arms. Named ``default-delivery`` so ingest resolves to it."""
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": build_script(landed_file),
                "produces": [NUDGE_PRODUCES_NAME, *_GIT_COMMIT_PRODUCES],
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
    """A source graph (`default-delivery`) whose `build` node migrates the chunk to
    `triage-delivery` (#90) instead of delivering in place — a no-op prompt, since the
    real commit + deliver happens at the target's own `build` after the re-queue."""
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
    whose `build` node name-matches the source's, so the migration lands there."""
    return graph_yaml(landed_file).replace("name: default-delivery", "name: triage-delivery", 1)


def migrate_hub_source_yaml() -> str:
    """A source graph (`default-delivery`, so ingest pins it) whose `build` migrates to the
    hub-landing target `triage-hub` (issue #111) rather than the runner-landing
    `triage-delivery` :func:`migrate_source_yaml` uses. Same no-op build prompt: the source
    node commits nothing, so the target's landing hub node has no branches to merge — the
    scenario asserts on convergence, not a landed file (see the test)."""
    return migrate_source_yaml().replace("graph:triage-delivery", "graph:triage-hub", 1)


def migrate_hub_target_yaml() -> str:
    """The hub-landing migration target (`triage-hub`, issue #111): its **entry** node
    `build` name-matches the source's migrating `build`, so the migration lands there, and
    is **hub-executed** via :data:`LAND_STEP`. This graph declares no `git_commit` `produces:`,
    so D1's empty-delivery refusal never arms here — with nothing submitted, the step stays a
    clean no-op that prints its success line and routes to `done`."""
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
    **intended**-migration crash scenario (issue #124) — no `graph:<name>` cross-graph edge
    anywhere, unlike :func:`migrate_source_yaml`; the migration is driven out of band by a
    PATCHed `intended_migration`. `deliver` is a dummy hub node, never actually reached:
    the scenario arms a `forced` intent naming the migration target's own `build` node."""
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


# Process helpers


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


# The forge (session) and the two daemons (per point)


@contextlib.contextmanager
def forge_daemon(bin_dir: Path, origins: Path, port: int) -> Iterator[httpx.Client]:
    log = daemon_log_sink(origins.parent / "forge.log")
    proc = subprocess.Popen(
        [str(bin_dir / "blizzard-mock-forge"), "--repos-dir", str(origins), "--host", "127.0.0.1", "--port", str(port)],
        stdout=log,
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
    work_sources: Sequence[WorkSourceConfig] | None = None,
    new_session: bool = False,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    """Start (or restart) the hub daemon; arm ``crash_point`` when it is a deliver point.

    ``new_session`` makes the hub a process-group leader so a caller can ``os.killpg`` the
    whole tree, including any spawned ``run:`` subprocess — required for a faithful
    kill -9 mid-script (issue #67)."""
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    if not (hub_dir / "blizzard-hub.toml").exists():
        subprocess.run([hub_bin, "init", str(hub_dir)], check=True, capture_output=True, text=True)
        write_work_sources(hub_dir, work_sources if work_sources is not None else default_work_sources(forge_port))
    env = {
        **os.environ,
        "BZ_FORGE_URL": f"http://127.0.0.1:{forge_port}",
        "BZ_FORGE_OWNER": OWNER,
        WORK_SOURCE_TOKEN_ENV: "crash-fixture-token",
    }
    _apply_crash_env(env, crash_point)
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [hub_bin, "host", "--dir", str(hub_dir), "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=daemon_log_sink(hub_dir / "daemon.log"),
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
        # The mock façade rejects an unknown ``--permission-mode`` flag, so it must be
        # omitted here — ``None`` omits it.
        harness_permission_mode=None,
        # Unset on purpose: the external-usage sampler's first soft-failure check (a
        # missing credentials file) trips before any request is built (issue #218).
        external_usage_credentials_path=str(runner_dir / "no-such-credentials.json"),
        base_branch="main",
        # `start_runner` sets `ENV_HARNESS_FENCE` in the daemon subprocess's own env; it
        # reaches a worker only because it is declared here (issue #88).
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
        stdout=daemon_log_sink(runner_dir / "daemon.log"),
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
