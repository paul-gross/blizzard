"""The OpenCode 2 lane of the kill-9 sweep (``bzh:crash-sweep``): a runner whose configured binary is an emitted
``mock-opencode-2`` engine, so its version composes OpenCode 2 and every turn is a driver on that engine's managed
service. The daemon runs under an isolated ``HOME``, so the service it starts, the drivers it launches, and this module's
reads all name one registration, and never the operator's own service."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import httpx2

from blizzard.runner.harness.opencode_v2.client import ServiceRequestError
from blizzard.runner.harness.opencode_v2.driver import DriverCore
from blizzard.runner.harness.opencode_v2.registration import ServiceRegistration, read_registration
from blizzard.runner.harness.opencode_v2.transport import UrllibServiceTransport
from tests.crash.support import ENV_HARNESS_FENCE, REPO, REPO_NAME, land_node
from tests.support.fleet_world import BUILD_BRANCH

#: The graph-level named session an OpenCode 2 ``build`` (and ``review``) node resumes, constrained to ``opencode``.
OPENCODE2_SESSION_NAME = "crash-sweep-opencode2"
#: The ``git_commit`` ``produces:`` a committing build declares.
_GIT_COMMIT_PRODUCES = [{"name": "commit", "kind": "git_commit"}]
_BLIZZARD = str(Path(sys.executable).parent / "blizzard")


def script(*lines: str) -> str:
    """A prompt the engine runs: the runner composes prose around every turn's message, so the script is tagged."""
    return "\n".join(["<behavior-script>", *lines, "</behavior-script>", ""])


def once(marker: Path, seconds: int) -> str:
    """A step that, the first time any turn reaches it, touches ``marker`` and holds for ``seconds`` before touching
    ``marker.slept``; every later turn passes straight through. A turn stopped mid-hold never writes ``.slept``."""
    return f"shell({f'if [ -e {marker} ]; then :; else touch {marker}; sleep {seconds}; touch {marker}.slept; fi'!r})"


def commit_and_declare(landed_file: str) -> str:
    """The build's commit, push, and declaration on :data:`BUILD_BRANCH`, idempotent so a retry after a stopped turn
    lands it once."""
    command = (
        "set -e\n"
        f"cd {REPO_NAME}\n"
        # The worktree arrives detached; recreating the branch is what a re-run of the node needs.
        f"git switch -q -C {BUILD_BRANCH}\n"
        f"echo 'landed by the crash sweep' > {landed_file}\n"
        "git add -A\n"
        'if [ -n "$(git status --porcelain)" ]; then '
        "git -c user.email=mock@blizzard.local -c user.name='Mock Harness' "
        "commit -q -m 'feat: land a change from the crash sweep'; fi\n"
        "branch=$(git rev-parse --abbrev-ref HEAD)\n"
        'git push -q --force-with-lease origin "$branch"\n'
        f'{_BLIZZARD} runner artifact commit --repo {REPO_NAME} --branch "$branch" --commit "$(git rev-parse HEAD)"\n'
    )
    return f"shell({command!r})"


def judge(*lines: str) -> str:
    """A judgement turn running ``lines`` and then passing."""
    return script(*lines, "text('<Choice>pass</Choice> the change is committed')")


def graph_yaml(build: str, *, judgement: str | None = None, review: str | None = None) -> str:
    """``build -> [review ->] deliver``, every runner node resuming :data:`OPENCODE2_SESSION_NAME` — so ``review``'s
    turn is a resume of the session ``build`` minted."""
    import yaml

    def node(prompt: str, to: str, *, produces: bool) -> dict[str, object]:
        body: dict[str, object] = {
            "executor": "runner",
            "session": f"resume:{OPENCODE2_SESSION_NAME}",
            "prompt": prompt,
            "judgement": {
                "prompt": judgement or judge(),
                "choices": {"pass": {"description": "The change is committed.", "to": to}},
            },
            "retries": {"max": 1, "exhausted": "escalate"},
        }
        if produces:
            body["produces"] = _GIT_COMMIT_PRODUCES
        return body

    nodes: dict[str, object] = {"build": node(build, "review" if review is not None else "deliver", produces=True)}
    if review is not None:
        nodes["review"] = node(review, "deliver", produces=False)
    nodes["deliver"] = land_node()
    graph = {
        "name": "default-delivery",
        "entry": "build",
        "sessions": {OPENCODE2_SESSION_NAME: {"harnesses": ["opencode"]}},
        "nodes": nodes,
    }
    return yaml.safe_dump(graph, sort_keys=False)


def ingest(hub: httpx2.Client, forge: httpx2.Client, definition: str, title: str) -> str:
    """Mint ``definition``, file an issue titled ``title``, and ingest it to a ready chunk."""
    minted = hub.post("/api/graphs", json={"definition_yaml": definition})
    assert minted.status_code == 201, minted.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": title, "body": "an OpenCode 2 crash-sweep chunk"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    return chunk_id


@dataclass(frozen=True)
class Service:
    """The engine artifact and the isolated home its managed service registers under."""

    binary: Path
    home: Path

    @classmethod
    def emit(cls, bin_dir: Path, root: Path, levers: Sequence[str] = ()) -> Service:
        home = root / "home"
        home.mkdir(parents=True)
        binary = root / "engine" / "opencode"
        binary.parent.mkdir(parents=True)
        command = [str(bin_dir / "mock-opencode-2"), "emit-engine", "--out", str(binary)]
        for lever in levers:
            command += ["--lever", lever]
        subprocess.run(command, env=cls._env(home), check=True, capture_output=True, text=True, timeout=120)
        return cls(binary=binary, home=home)

    @staticmethod
    def _env(home: Path) -> dict[str, str]:
        return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(home), ENV_HARNESS_FENCE: "1"}

    def daemon_env(self) -> dict[str, str]:
        """What the runner daemon runs under, so everything it starts names this service."""
        return {"HOME": str(self.home)}

    def registration(self) -> ServiceRegistration | None:
        return read_registration(self.home / ".local" / "state" / "opencode" / "service.json")

    def running(self, session_id: str) -> bool:
        """Whether ``session_id``'s tree runs anything on the service now."""
        return bool(
            DriverCore(self.registration, UrllibServiceTransport(), poll_seconds=0.0).tree_state(session_id).running
        )

    def idle(self, session_id: str) -> bool:
        """Whether nothing of ``session_id`` runs: no service registered, no such session, or its tree settled."""
        if self.registration() is None:
            return True
        try:
            return not self.running(session_id)
        except ServiceRequestError as exc:
            if exc.status == 404:
                return True
            raise

    def stop(self) -> None:
        """Stop the service, so no test leaves one behind; one never started is already stopped."""
        subprocess.run(
            [str(self.binary), "service", "stop"], env=self._env(self.home), capture_output=True, text=True, timeout=60
        )


def await_path(path: Path, *, timeout: float = 90.0, proc: subprocess.Popen[str] | None = None) -> None:
    """Wait until ``path`` exists; fail early if ``proc`` (the daemon) has died."""
    deadline = time.monotonic() + timeout
    while not path.exists():
        if proc is not None and proc.poll() is not None:
            raise AssertionError(f"the runner daemon exited ({proc.returncode}) before {path.name} appeared")
        if time.monotonic() > deadline:
            raise AssertionError(f"{path.name} never appeared")
        time.sleep(0.2)


def await_true(check: Callable[[], bool], what: str, *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting until {what}")
        time.sleep(0.2)


__all__ = [
    "OPENCODE2_SESSION_NAME",
    "Service",
    "await_path",
    "await_true",
    "commit_and_declare",
    "graph_yaml",
    "ingest",
    "judge",
    "once",
    "script",
]
