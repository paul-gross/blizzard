"""Join a runner directory to a live hub the way an operator does — ``blizzard runner init <dir> --hub <url>``
in its own process, against a hub on ``auth.mode = "none"`` (or blizzard-mock's mirror of the add route) —
and the environment every runner daemon a subprocess tier spawns starts under.

The init runs out of process on purpose: an in-process ``runner init`` joins the suite-wide fake hub
(``tests/runner_init_fakes.py``), and these tiers prove the real wire."""

from __future__ import annotations

import os
import subprocess
import sys
import weakref
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx

from blizzard.runner.config import DEFAULT_TOKEN_ENV, ENV_HUB_URL
from blizzard.runner.hub.token_file import HubTokenFile

RUNNER_BIN = Path(sys.executable).parent / "blizzard-runner"


@dataclass(frozen=True)
class JoinedRunner:
    """The identity the hub answers for the token ``runner init`` left in the runner directory."""

    runner_id: str
    runner_name: str
    token: str

    @property
    def bearer(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


def add_runner(hub: httpx.Client, name: str) -> tuple[str, str]:
    """Add a runner at ``hub`` through ``POST /api/runners`` — no sign-in on ``auth.mode = "none"`` or
    blizzard-mock's hub, else ``hub`` must carry an operator session allowed to add runners — and
    answer its minted id and the token issued with it."""
    added = hub.post("/api/runners", json={"name": name})
    assert added.status_code == 201, added.text
    return str(added.json()["runner_id"]), str(added.json()["token"])


#: The tokens :func:`fleet_headers` added runners for, per hub client and runner name.
_FLEET_DRIVER_TOKENS: weakref.WeakKeyDictionary[httpx.Client, dict[str, str]] = weakref.WeakKeyDictionary()


def fleet_headers(hub: httpx.Client, name: str = "fleet-driver") -> dict[str, str]:
    """The bearer a test presents to drive ``/api/fleet/*`` straight against ``hub`` — the fleet
    answers only a runner — for the runner added there under ``name`` on first use and reused for the
    client's lifetime, so each name a test drives the fleet as is one runner."""
    tokens = _FLEET_DRIVER_TOKENS.setdefault(hub, {})
    if name not in tokens:
        _runner_id, tokens[name] = add_runner(hub, name)
    return {"Authorization": f"Bearer {tokens[name]}"}


def runner_spawn_env(
    hub_url: str, extra: Mapping[str, str] | None = None, *, base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """``base`` (this process's environment by default) with ``hub_url`` pinned as ``BZ_HUB_URL`` and any
    ambient hub token scrubbed, then ``extra`` laid over — so a runner presents only its ``.env`` token. The
    pin seeds only a fresh scaffold: ``runner host`` takes its hub from the directory's config."""
    env = {key: value for key, value in (os.environ if base is None else base).items() if key != DEFAULT_TOKEN_ENV}
    env[ENV_HUB_URL] = hub_url
    env.update(extra or {})
    return env


def run_runner_init(
    runner_dir: Path, hub_url: str, *, allow_readd: bool = False, pin_hub: bool = True
) -> subprocess.CompletedProcess[str]:
    """``blizzard runner init <runner_dir>`` against ``hub_url``; the completed process, refusals included.
    ``pin_hub=False`` leaves ``--hub`` off, so the hub comes from the directory's config or ``BZ_HUB_URL``."""
    args = [str(RUNNER_BIN), "init", str(runner_dir)]
    if pin_hub:
        args += ["--hub", hub_url]
    if allow_readd:
        args.append("--allow-readd")
    return subprocess.run(args, capture_output=True, text=True, env=runner_spawn_env(hub_url), check=False, timeout=60)


def held_token(runner_dir: Path) -> str:
    """The hub token the runner directory's ``.env`` holds — empty when it holds none."""
    return HubTokenFile.of(runner_dir, DEFAULT_TOKEN_ENV).held()


def token_identity(hub_url: str, token: str) -> httpx.Response:
    """``GET /api/fleet/identity`` under ``token`` — side-effect free, so a probe never registers."""
    return httpx.get(f"{hub_url}/api/fleet/identity", headers={"Authorization": f"Bearer {token}"}, timeout=15.0)


def join_runner(runner_dir: Path, hub_url: str, *, allow_readd: bool = False) -> JoinedRunner:
    """Run ``runner init`` against ``hub_url`` and answer the identity its written token resolves to.
    ``allow_readd`` is for a runner directory reused across a reset of its hub's data."""
    done = run_runner_init(runner_dir, hub_url, allow_readd=allow_readd)
    assert done.returncode == 0, f"runner init failed ({done.returncode}):\n{done.stdout}\n{done.stderr}"
    return _held_identity(runner_dir, hub_url)


def adopt_runner(runner_dir: Path, hub_url: str, token: str) -> JoinedRunner:
    """Join a runner an operator already added at a hub with sign-in: put the token its add printed
    in the runner directory's ``.env``, then ``runner init``, which finds it known and adds nothing."""
    runner_dir.mkdir(parents=True, exist_ok=True)
    HubTokenFile.of(runner_dir, DEFAULT_TOKEN_ENV).write(token)
    joined = join_runner(runner_dir, hub_url)
    assert joined.token == token, "runner init replaced the token of a runner the hub already knows"
    return joined


def pin_runner(runner_dir: Path, *, runner_id: str, name: str) -> JoinedRunner:
    """Fix a runner's id and token up front, for a blizzard-mock hub that comes up — or comes back with
    its in-memory state gone — only after the runner directory exists: the token goes into the
    directory's ``.env`` now, and :func:`seed_pinned_runner` adds the same pair at each such hub."""
    runner_dir.mkdir(parents=True, exist_ok=True)
    pinned = JoinedRunner(runner_id=runner_id, runner_name=name, token=f"tok_{runner_id}")
    HubTokenFile.of(runner_dir, DEFAULT_TOKEN_ENV).write(pinned.token)
    return pinned


def seed_pinned_runner(hub: httpx.Client, runner: JoinedRunner) -> None:
    """Add ``runner`` at a blizzard-mock ``hub`` under its pinned id and token (``POST /_seed/runners``)."""
    seeded = hub.post(
        "/_seed/runners", json={"name": runner.runner_name, "runner_id": runner.runner_id, "token": runner.token}
    )
    assert seeded.status_code == 201, seeded.text


def _held_identity(runner_dir: Path, hub_url: str) -> JoinedRunner:
    token = held_token(runner_dir)
    assert token, f"runner init wrote no {DEFAULT_TOKEN_ENV} to {runner_dir / '.env'}"
    answer = token_identity(hub_url, token)
    assert answer.status_code == 200, answer.text
    body = answer.json()
    return JoinedRunner(runner_id=body["runner_id"], runner_name=body["runner_name"], token=token)
