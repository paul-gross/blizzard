"""Runner runtime configuration — resolved from a runtime directory.

``blizzard runner init <dir>`` scaffolds a config file and a data directory; the
daemon and the offline ``migrate`` verb read it back. The store URL is the single
portability knob (``bzh:sql-portable``): sqlite (WAL, in-process) is the runner's
embedded default (D-023/D-028). The bind port falls back to the winter service
band's ``BZ_RUNNER_PORT`` (band +3).
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

CONFIG_FILENAME = "blizzard-runner.toml"
DATA_DIRNAME = "data"
# The runner-owned worker hook file `init` scaffolds (design/harness-adapters.md); the
# adapter passes it to a spawned worker as `--settings` to deliver the heartbeat hook.
WORKER_SETTINGS_FILENAME = "worker-settings.json"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8431

ENV_HOST = "BZ_RUNNER_HOST"
ENV_PORT = "BZ_RUNNER_PORT"
ENV_HUB_URL = "BZ_HUB_URL"
# The reconciliation-loop seams the winter service injects per feature env so a fresh
# `blizzard runner init` scaffolds a runnable config without hand-editing the toml
# (the winter-service-tmux runner slot sets these from the env band + fixture paths).
ENV_WORKSPACE_ROOT = "BZ_WORKSPACE_ROOT"
ENV_WORKSPACE_ENVS = "BZ_WORKSPACE_ENVS"  # comma-separated env-id pool
ENV_HARNESS_BINARY = "BZ_HARNESS_BINARY"
ENV_HARNESS_PERMISSION_MODE = "BZ_HARNESS_PERMISSION_MODE"
ENV_BASE_BRANCH = "BZ_BASE_BRANCH"
ENV_GATES = "BZ_RUNNER_GATES"  # comma-separated node names this runner gates (D-032/D-073)
ENV_WORKSPACE_PROMPT = "BZ_WORKSPACE_PROMPT"  # the runner-owned workspace prompt, inline (issue #17)

# Reconciliation-loop defaults (design/runner/loop.md). The runner is machine-level
# and single-workspace (D-019); these seam the loop to the hub, the workspace it
# drives, and the coding harness it spawns.
DEFAULT_HUB_URL = "http://127.0.0.1:8421"  # the hub's default bind (band +2)
DEFAULT_RUNNER_ID = "runner-local"
DEFAULT_WORKSPACE_ID = "workspace-local"
DEFAULT_HARNESS_BINARY = "claude"
# A workspace-isolated worker runs headless with no one to approve tool use, so real
# Claude Code needs a non-interactive permission mode to edit/commit in its sandboxed
# worktree (design/harness-adapters.md, D-092); ``bypassPermissions`` is the fresh-init
# default. The ``mock-claude-code`` façade ignores it; a config may set it empty to omit.
DEFAULT_HARNESS_PERMISSION_MODE = "bypassPermissions"
DEFAULT_MAX_AGENTS = 1
DEFAULT_BASE_BRANCH = "main"
DEFAULT_ENV_POOL: tuple[str, ...] = ("e1",)


class ConfigError(RuntimeError):
    """A runtime directory is missing its config — it was never initialized."""


@dataclass(frozen=True)
class RunnerConfig:
    """Resolved runner runtime configuration."""

    root: Path
    db_url: str
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    # Reconciliation-loop seams (design/runner/loop.md).
    hub_url: str = DEFAULT_HUB_URL
    runner_id: str = DEFAULT_RUNNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    workspace_root: str = ""  # the winter workspace the provider drives; required to FILL
    workspace_envs: tuple[str, ...] = DEFAULT_ENV_POOL  # the provider's static env pool (D-062)
    harness_binary: str = DEFAULT_HARNESS_BINARY  # mock-claude-code in tests, `claude` in prod (D-092)
    harness_permission_mode: str | None = None  # `claude -p --permission-mode` (headless); None omits it
    worker_settings_path: str | None = None  # the runner-owned worker hook file (P7)
    max_agents: int = DEFAULT_MAX_AGENTS
    base_branch: str = DEFAULT_BASE_BRANCH
    #: Node NAMES this runner imposes a human gate on (D-032/D-041/D-073). Reloaded each
    #: tick — the loop rebuilds its context from this config on every pass.
    gates: tuple[str, ...] = ()
    #: The runner-owned workspace prompt prepended to every worker spawn (issue #17): the
    #: standing "you are a fleet worker in this winter workspace" framing above the node
    #: envelope. Two source knobs, one effective value (:meth:`resolved_workspace_prompt`):
    #: ``workspace_prompt`` is the inline text; ``workspace_prompt_file`` is a path (absolute,
    #: or relative to :attr:`root`) whose contents win over the inline text when set. Empty
    #: on a fresh scaffold — an absent prompt still spawns a valid worker (table-only). The
    #: runtime override (local API, no restart) lives in the store, not here.
    workspace_prompt: str = ""
    workspace_prompt_file: str = ""

    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_FILENAME

    @property
    def data_dir(self) -> Path:
        return self.root / DATA_DIRNAME

    @staticmethod
    def default_db_url(root: Path) -> str:
        return f"sqlite:///{(root / DATA_DIRNAME / 'runner.db').resolve()}"

    def resolved_workspace_prompt(self) -> str:
        """The effective static workspace prompt (issue #17), resolved from its two knobs.

        ``workspace_prompt_file`` wins when set — read once at ``host`` startup, so the
        prompt file is loaded (not re-read per spawn); a relative path resolves under
        :attr:`root`. A configured-but-missing file is an operator error and raises here
        (fail fast at startup), which is not the same as an *absent* prompt — both knobs
        empty is a valid, table-only spawn and returns ``""``.
        """
        if self.workspace_prompt_file:
            path = Path(self.workspace_prompt_file)
            if not path.is_absolute():
                path = self.root / path
            if not path.exists():
                raise ConfigError(f"workspace_prompt_file does not exist: {path}")
            return path.read_text()
        return self.workspace_prompt

    @classmethod
    def scaffold(cls, root: Path) -> RunnerConfig:
        """The default config for a fresh runtime root (used by ``init``).

        The loop seams (workspace root, env pool, harness binary, base branch) are read
        from the winter-injected environment when present so the service's
        ``blizzard runner init`` produces a runnable config; each falls back to its
        dataclass default otherwise.
        """
        envs = os.environ.get(ENV_WORKSPACE_ENVS)
        gates = os.environ.get(ENV_GATES)
        return cls(
            root=root,
            db_url=cls.default_db_url(root),
            host=os.environ.get(ENV_HOST, DEFAULT_HOST),
            port=int(os.environ.get(ENV_PORT, DEFAULT_PORT)),
            hub_url=os.environ.get(ENV_HUB_URL, DEFAULT_HUB_URL),
            workspace_root=os.environ.get(ENV_WORKSPACE_ROOT, ""),
            workspace_envs=_as_env_tuple([e.strip() for e in envs.split(",") if e.strip()])
            if envs
            else DEFAULT_ENV_POOL,
            harness_binary=os.environ.get(ENV_HARNESS_BINARY, DEFAULT_HARNESS_BINARY),
            harness_permission_mode=os.environ.get(ENV_HARNESS_PERMISSION_MODE, DEFAULT_HARNESS_PERMISSION_MODE)
            or None,
            base_branch=os.environ.get(ENV_BASE_BRANCH, DEFAULT_BASE_BRANCH),
            gates=tuple(g.strip() for g in gates.split(",") if g.strip()) if gates else (),
            # The worker hook file `init` writes alongside the config; the adapter
            # delivers it as `--settings` so a spawned worker heartbeats (D-069).
            worker_settings_path=str(root / WORKER_SETTINGS_FILENAME),
            # The runner-owned workspace prompt (issue #17): empty on a fresh scaffold —
            # an operator sets it inline (or points `workspace_prompt_file` at a file), or
            # replaces it at runtime through the local API. Seeded from the environment so a
            # service's `blizzard runner init` can inject a default without hand-editing.
            workspace_prompt=os.environ.get(ENV_WORKSPACE_PROMPT, ""),
        )

    def to_toml(self) -> str:
        envs = ", ".join(f'"{e}"' for e in self.workspace_envs)
        gates = ", ".join(f'"{g}"' for g in self.gates)
        settings = f'"{self.worker_settings_path}"' if self.worker_settings_path else '""'
        # `json.dumps` emits a valid TOML basic string: TOML shares JSON's escapes
        # (\n, \t, \", \\, \uXXXX), so a multi-line inline prompt round-trips intact.
        workspace_prompt = json.dumps(self.workspace_prompt)
        workspace_prompt_file = json.dumps(self.workspace_prompt_file)
        return (
            "# blizzard-runner runtime configuration (blizzard runner init)\n"
            f'db_url = "{self.db_url}"\n'
            f'host = "{self.host}"\n'
            f"port = {self.port}\n"
            "\n# Reconciliation-loop seams (design/runner/loop.md).\n"
            f'hub_url = "{self.hub_url}"\n'
            f'runner_id = "{self.runner_id}"\n'
            f'workspace_id = "{self.workspace_id}"\n'
            f'workspace_root = "{self.workspace_root}"\n'
            f"workspace_envs = [{envs}]\n"
            f'harness_binary = "{self.harness_binary}"\n'
            f'harness_permission_mode = "{self.harness_permission_mode or ""}"\n'
            f"worker_settings_path = {settings}\n"
            f"max_agents = {self.max_agents}\n"
            f'base_branch = "{self.base_branch}"\n'
            "\n# Human gates this runner imposes by node name (D-032/D-073); empty = none.\n"
            f"gates = [{gates}]\n"
            "\n# The runner-owned workspace prompt prepended to every worker spawn (issue #17).\n"
            "# `workspace_prompt` is inline text; `workspace_prompt_file` (a path) wins when set.\n"
            "# Empty = table-only injection. Replace at runtime via PUT /api/workspace-prompt.\n"
            f"workspace_prompt = {workspace_prompt}\n"
            f"workspace_prompt_file = {workspace_prompt_file}\n"
        )

    @classmethod
    def load(cls, root: Path, *, host: str | None = None, port: int | None = None) -> RunnerConfig:
        """Read a runtime root's config file; overlay CLI host/port when given."""
        root = root.resolve()
        path = root / CONFIG_FILENAME
        if not path.exists():
            raise ConfigError(f"{root} is not an initialized runner runtime (run `blizzard runner init {root}`)")
        raw = tomllib.loads(path.read_text())
        return cls(
            root=root,
            db_url=str(raw["db_url"]),
            host=host or str(raw.get("host", DEFAULT_HOST)),
            port=port if port is not None else int(raw.get("port", DEFAULT_PORT)),
            hub_url=str(raw.get("hub_url", DEFAULT_HUB_URL)),
            runner_id=str(raw.get("runner_id", DEFAULT_RUNNER_ID)),
            workspace_id=str(raw.get("workspace_id", DEFAULT_WORKSPACE_ID)),
            workspace_root=str(raw.get("workspace_root", "")),
            workspace_envs=_as_env_tuple(raw.get("workspace_envs", DEFAULT_ENV_POOL)),
            harness_binary=str(raw.get("harness_binary", DEFAULT_HARNESS_BINARY)),
            harness_permission_mode=(str(raw["harness_permission_mode"]) or None)
            if raw.get("harness_permission_mode")
            else None,
            worker_settings_path=(str(raw["worker_settings_path"]) or None)
            if raw.get("worker_settings_path")
            else None,
            max_agents=int(raw.get("max_agents", DEFAULT_MAX_AGENTS)),
            base_branch=str(raw.get("base_branch", DEFAULT_BASE_BRANCH)),
            gates=tuple(str(g) for g in raw.get("gates", ())),
            workspace_prompt=str(raw.get("workspace_prompt", "")),
            workspace_prompt_file=str(raw.get("workspace_prompt_file", "")),
        )


def _as_env_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return DEFAULT_ENV_POOL
