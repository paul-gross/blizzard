"""The daemons and verbs a rehearsal runs under an installation — always as subprocesses.

Nothing here imports the installation: its config comes from its own ``init``, then edits to the
TOML text, so a baseline's directories never carry HEAD's shapes."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

from blizzard.tools.release_install import Installation
from tests.fleet_launchers import await_http, terminate
from tests.runner_join import runner_spawn_env
from tests.support import daemon_log_sink, free_port, write_mock_harness_credentials

#: The mock harness's fence variable, which a worker reaches only through the runner's passthrough list.
FENCE_VAR = "BLIZZARD_MOCK_HARNESS_FENCE"
_HUB_DB_VAR = "BZ_HUB_DB_URL"


class StepFailure(AssertionError):
    """A rehearsal failure, attributed to the step, the baseline, and the backend it happened under."""


@dataclass(frozen=True)
class Leg:
    """Whose upgrade this is: the attribution every failure carries."""

    baseline: str
    backend: str

    def fail(self, step: str, detail: str) -> StepFailure:
        return StepFailure(f"[{step}, {self.baseline}, {self.backend}] {detail}")


@dataclass
class Hub:
    """One hub directory, hosted by whichever installation is given."""

    dir: Path
    port: int
    db_url: str | None
    proc: subprocess.Popen[str] | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def store_url(self) -> str:
        return self.db_url or f"sqlite:///{self.dir / 'data' / 'hub.db'}"

    def _db_env(self) -> dict[str, str]:
        return {_HUB_DB_VAR: self.db_url} if self.db_url else {}

    def _db_flags(self) -> list[str]:
        return ["--allow-external-db"] if self.db_url else []

    def init(self, installation: Installation) -> None:
        done = installation.run(
            "blizzard-hub", "init", str(self.dir), *self._db_flags(), env=installation.env(self._db_env())
        )
        assert done.returncode == 0, f"{installation.label} hub init failed:\n{done.stdout}\n{done.stderr}"

    def migrate(self, installation: Installation, *, down: str | None = None) -> subprocess.CompletedProcess[str]:
        args = [
            "blizzard-hub",
            "migrate",
            "--dir",
            str(self.dir),
            *self._db_flags(),
            *(["--down", down] if down else []),
        ]
        return installation.run(*args, env=installation.env(self._db_env()))

    def start(self, installation: Installation) -> httpx.Client:
        """Host the directory under ``installation`` and answer a client once ``/api/ready`` does."""
        log = self.dir / "daemon.log"
        self.proc = subprocess.Popen(
            [str(installation.bin_dir / "blizzard-hub"), "host", "--dir", str(self.dir), "--host", "127.0.0.1",
             "--port", str(self.port), *self._db_flags()],
            env=installation.env(self._db_env()), stdout=daemon_log_sink(log), stderr=subprocess.STDOUT, text=True,
        )  # fmt: skip
        client = httpx.Client(base_url=self.url, timeout=30.0)
        await_http(self.proc, client, "/api/ready", log=log)
        return client

    def stop(self) -> None:
        if self.proc is not None:
            terminate(self.proc)
            self.proc = None


@dataclass
class Host:
    """One runner host directory, hosted by whichever installation is given."""

    dir: Path
    port: int
    proc: subprocess.Popen[str] | None = None

    @property
    def store_url(self) -> str:
        return f"sqlite:///{self.dir / 'data' / 'host.db'}"

    def scaffold(self, installation: Installation) -> None:
        """``runner init`` against a hub nothing answers at: the directory is written, nothing is registered."""
        dead = f"http://127.0.0.1:{free_port()}"
        installation.run(
            "blizzard-runner", "init", str(self.dir), "--hub", dead, env=runner_spawn_env(dead, base=installation.env())
        )
        assert (self.dir / "blizzard-host.toml").is_file(), f"{installation.label} runner init wrote no host file"

    def init(self, installation: Installation, hub_url: str) -> None:
        """``runner init``, which registers the host and its runner at ``hub_url`` (or the hub a scaffold names)."""
        env = runner_spawn_env(hub_url, base=installation.env())
        done = installation.run("blizzard-runner", "init", str(self.dir), "--hub", hub_url, env=env)
        assert done.returncode == 0, f"{installation.label} runner init failed:\n{done.stdout}\n{done.stderr}"

    def migrate(self, installation: Installation, *, down: str | None = None) -> subprocess.CompletedProcess[str]:
        return installation.run(
            "blizzard-runner", "migrate", "--dir", str(self.dir), *(["--down", down] if down else [])
        )

    def start(self, installation: Installation, hub_url: str) -> None:
        """Host the directory under ``installation`` until its ``/api/ready`` answers."""
        log = self.dir / "daemon.log"
        env = runner_spawn_env(hub_url, {"BZ_RUNNER_TICK_SECONDS": "0.3", FENCE_VAR: "1"}, base=installation.env())
        self.proc = subprocess.Popen(
            [str(installation.bin_dir / "blizzard-runner"), "host", "--dir", str(self.dir)],
            env=env, stdout=daemon_log_sink(log), stderr=subprocess.STDOUT, text=True,
        )  # fmt: skip
        with httpx.Client(base_url=f"http://127.0.0.1:{self.port}", timeout=30.0) as client:
            await_http(self.proc, client, "/api/ready", log=log)

    def stop(self) -> None:
        if self.proc is not None:
            terminate(self.proc)
            self.proc = None


def shape_host_file(host: Host, *, name: str, hub_url: str, project: str, workspace: Path, mock_bin: Path) -> None:
    """Point a scaffolded host file at the hub, the fixture workspace, and the mock harness, by editing its text."""
    claude, opencode = write_mock_harness_credentials(host.dir)
    origin = workspace.parent / "origins" / "toy-api.git"
    path = host.dir / "blizzard-host.toml"
    text = path.read_text()
    for pattern, replacement in (
        (r'^hub_url = ".*"$', f'hub_url = "{hub_url}"'),
        (r"^port = \d+$", f"port = {host.port}"),
        (
            r"^sample_interval_seconds = (\d+)$",
            rf'sample_interval_seconds = \1\ncredentials_path = "{host.dir / "no-such-credentials.json"}"',
        ),
        (r'^root = ""$', f'root = "{workspace}"'),
        (
            r'^prompt_package = ""$',
            f'prompt_package = ""\n\n[[workspace.repo]]\nname = "toy-api"\nurl = "file://{origin}"',
        ),
        (r'^name = "runner-local"$', f'name = "{name}"'),
        (r'project = "default"', f'project = "{project}"'),
        (r'^transcripts_root = ""$', f'transcripts_root = "{workspace / ".blizzard-mock-harness" / "transcripts"}"'),
        (r'^(worker_settings_path = ".*")$', rf'\1\nclaude_code_credentials_path = "{claude}"'),
        (r'^binary = "claude"$', f'binary = "{mock_bin / "mock-claude-code"}"'),
        (r'^binary = "opencode"$', f'binary = "{mock_bin / "mock-opencode"}"\nauth_path = "{opencode}"'),
        (r"^env_passthrough = \[\]$", f'env_passthrough = ["{FENCE_VAR}"]'),
    ):
        text, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
        assert count == 1, f"{path} has no line matching {pattern!r}"
    path.write_text(text)
