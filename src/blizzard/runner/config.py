"""Runner runtime configuration — resolved from a runtime directory.

``blizzard runner init <dir>`` scaffolds a config file and a data directory; the
daemon and the offline ``migrate`` verb read it back. The store URL is the single
portability knob (``bzh:sql-portable``): sqlite (WAL, in-process) is the runner's
embedded default. The bind port falls back to the winter service
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
# The runner-owned worker hook file `init` scaffolds; the
# adapter passes it to a spawned worker as `--settings` to deliver the heartbeat hook.
WORKER_SETTINGS_FILENAME = "worker-settings.json"
# The local API's unix socket: it lives under the state dir beside the store, and
# filesystem permissions are its access control — so the CLI finds it from the runtime dir
# alone. The TCP listener runs alongside it for the browser, which cannot speak a socket.
SOCKET_FILENAME = "runner.sock"

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
ENV_GATES = "BZ_RUNNER_GATES"  # comma-separated node names this runner gates
ENV_WORKSPACE_PROMPT = "BZ_WORKSPACE_PROMPT"  # the runner-owned workspace prompt, inline (issue #17)
# Where the coding harness writes session transcripts (issue #29); empty defaults to
# `~/.claude/projects`, resolved once at the composition root (`runner/app.py`), never here.
ENV_TRANSCRIPTS_ROOT = "BZ_TRANSCRIPTS_ROOT"

# Reconciliation-loop defaults. The runner is machine-level
# and single-workspace; these seam the loop to the hub, the workspace it
# drives, and the coding harness it spawns.
DEFAULT_HUB_URL = "http://127.0.0.1:8421"  # the hub's default bind (band +2)
DEFAULT_RUNNER_ID = "runner-local"
DEFAULT_WORKSPACE_ID = "workspace-local"
DEFAULT_HARNESS_BINARY = "claude"
# A workspace-isolated worker runs headless with no one to approve tool use, so real
# Claude Code needs a non-interactive permission mode to edit/commit in its sandboxed
# worktree; ``bypassPermissions`` is the fresh-init
# default. The ``mock-claude-code`` façade ignores it; a config may set it empty to omit.
DEFAULT_HARNESS_PERMISSION_MODE = "bypassPermissions"
DEFAULT_MAX_AGENTS = 1
DEFAULT_BASE_BRANCH = "main"
# The env var naming this runner's hub bearer token (issue #86b) — mirrors
# `PmSourceConfig.token_env` (`src/blizzard/hub/config.py`): the toml round-trips only the
# variable NAME, never the secret, which lives in the runtime env file (systemd
# `EnvironmentFile`, the orchestrator env in dev).
DEFAULT_TOKEN_ENV = "BZ_HUB_TOKEN"
DEFAULT_ENV_POOL: tuple[str, ...] = ("e1",)
# The runner-ceiling rolling window's default length (issue #61b) — used only when
# `runner_ceiling_usd` is set and `window_hours` is not given alongside it; a ceiling with
# no window still needs one to sum over, and a day is the least surprising default.
DEFAULT_RUNNER_CEILING_WINDOW_HOURS = 24.0


def socket_path_for(root: Path) -> Path:
    """The local API's socket under a runtime dir — derivable from the path alone.

    Deliberately not a method on the loaded config: it is what lets the CLI's local verbs
    address the daemon from ``--dir`` without reading the toml or opening the store — a
    pure client of the local API.
    """
    return root / SOCKET_FILENAME


class ConfigError(RuntimeError):
    """A runtime directory is missing its config — it was never initialized."""


@dataclass(frozen=True)
class RunnerConfig:
    """Resolved runner runtime configuration."""

    root: Path
    db_url: str
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    # Reconciliation-loop seams.
    hub_url: str = DEFAULT_HUB_URL
    runner_id: str = DEFAULT_RUNNER_ID
    workspace_id: str = DEFAULT_WORKSPACE_ID
    #: Names the env var carrying this runner's hub bearer token (issue #86b) — never the
    #: secret itself: round-trips through toml (mirrors ``PmSourceConfig.token_env``,
    #: ``src/blizzard/hub/config.py``). :attr:`hub_token` is the *resolved* secret, read
    #: from ``os.environ[token_env]`` at ``scaffold``/``load`` and never written back to
    #: toml. Empty (``hub_token == ""``) is a valid, warn-mode-only state — the outbound
    #: client attaches no ``Authorization`` header, so a fleet with no tokens installed yet
    #: keeps working.
    token_env: str = DEFAULT_TOKEN_ENV
    hub_token: str = ""
    workspace_root: str = ""  # the winter workspace the provider drives; required to FILL
    workspace_envs: tuple[str, ...] = DEFAULT_ENV_POOL  # the provider's static env pool
    harness_binary: str = DEFAULT_HARNESS_BINARY  # mock-claude-code in tests, `claude` in prod
    harness_permission_mode: str | None = None  # `claude -p --permission-mode` (headless); None omits it
    worker_settings_path: str | None = None  # the runner-owned worker hook file (P7)
    max_agents: int = DEFAULT_MAX_AGENTS
    base_branch: str = DEFAULT_BASE_BRANCH
    #: Node NAMES this runner imposes a human gate on. Reloaded each
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
    #: Where the coding harness writes session transcripts (issue #29). Empty (the
    #: fresh-scaffold default) means ``~/.claude/projects`` — resolved once at the
    #: composition root (``runner/app.py``), never inside the transcript adapter.
    #: Seeded from ``BZ_TRANSCRIPTS_ROOT`` at ``runner init`` and then read from
    #: ``blizzard-runner.toml`` — **not** re-read from the environment live. That is
    #: safe only because the service orchestrator's per-env launch command deletes
    #: ``blizzard-runner.toml`` before every ``init`` (workspace
    #: ``.winter/config/winter-service-tmux/config.toml``), so a changed env var
    #: takes effect on the next service start; a bare process restart without that
    #: delete would keep the stale value.
    transcripts_root: str = ""
    #: The per-chunk spend cap (epic #57, issue #61a) — read from the ``[cost]`` table's
    #: ``chunk_cap_usd`` key. Absent (``None``, the fresh-scaffold default) means no cap,
    #: today's behavior unchanged. When set, ADVANCE's step boundary parks a chunk whose
    #: hub-derived total cost (``ChunkDetail.cost.cost_usd``) reaches or exceeds this value
    #: ``needs_human`` rather than spawning its next attempt, instead of killing the live
    #: worker that just finished it. The ``[cost]`` table is shared with
    #: ``runner_ceiling_usd`` — one section for the epic's two spend controls.
    chunk_cap_usd: float | None = None
    #: The runner-wide spend ceiling (epic #57, issue #61b) — read from the ``[cost]``
    #: table's ``runner_ceiling_usd`` key. Absent (``None``, the fresh-scaffold default)
    #: means no ceiling, today's behavior unchanged. When set, the tick's ceiling check
    #: (:func:`blizzard.runner.loop.steps.check_spend_ceiling`) sums this runner's own
    #: LOCAL usage facts over the trailing :attr:`runner_ceiling_window_hours` (a rolling
    #: window off the injected clock, never wall time) and, once that sum reaches this
    #: value, engages the existing local pause brake (the same one ``blizzard runner
    #: pause`` sets) rather than inventing a second suppression mechanism — every spawn
    #: site already honors it. There is no auto-unpause: the brake stays engaged even
    #: after the rolling window later drops back under the ceiling, until an operator
    #: consciously runs ``blizzard runner start``.
    runner_ceiling_usd: float | None = None
    #: The runner ceiling's rolling window length in hours (issue #61b) — read from
    #: ``[cost].window_hours``. Meaningless (and unused) while :attr:`runner_ceiling_usd`
    #: is ``None``; defaults to :data:`DEFAULT_RUNNER_CEILING_WINDOW_HOURS` when a ceiling
    #: is set but no window is given alongside it.
    runner_ceiling_window_hours: float = DEFAULT_RUNNER_CEILING_WINDOW_HOURS
    #: The operator's declared extension to the worker spawn-environment allowlist
    #: (issue #88) — read from the ``[worker]`` table's ``env_passthrough`` key. The
    #: adapter's three subprocess env constructions build from a fixed base allowlist
    #: (``PATH``/``HOME``/``USER``/``LANG``/``LC_*``/``TERM``/``TMPDIR``) plus this list,
    #: never a full ``os.environ`` copy — so a daemon secret (foremost ``BZ_HUB_TOKEN``)
    #: is absent from every worker/judge/resume child by construction unless an operator
    #: deliberately names it here. Empty on a fresh scaffold.
    worker_env_passthrough: tuple[str, ...] = ()

    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_FILENAME

    @property
    def data_dir(self) -> Path:
        return self.root / DATA_DIRNAME

    @property
    def socket_path(self) -> Path:
        """The local API's unix socket, under the state dir with the store."""
        return socket_path_for(self.root)

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

    def auth_headers(self) -> dict[str, str]:
        """The outbound ``Authorization`` header every runner->hub call carries (issue #86b).

        One credential path for the reconciliation loop's ``httpx.Client`` and the
        pm-items proxy alike, rather than each building its own header. Empty when
        :attr:`hub_token` is unset — an unenrolled runner (or a fleet that has not
        installed tokens yet) attaches nothing, and the hub's own ``runner_auth_mode``
        (``warn`` by default) decides whether that is tolerated.
        """
        if not self.hub_token:
            return {}
        return {"Authorization": f"Bearer {self.hub_token}"}

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
            token_env=DEFAULT_TOKEN_ENV,
            hub_token=os.environ.get(DEFAULT_TOKEN_ENV, ""),
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
            # delivers it as `--settings` so a spawned worker heartbeats.
            worker_settings_path=str(root / WORKER_SETTINGS_FILENAME),
            # The runner-owned workspace prompt (issue #17): empty on a fresh scaffold —
            # an operator sets it inline (or points `workspace_prompt_file` at a file), or
            # replaces it at runtime through the local API. Seeded from the environment so a
            # service's `blizzard runner init` can inject a default without hand-editing.
            workspace_prompt=os.environ.get(ENV_WORKSPACE_PROMPT, ""),
            transcripts_root=os.environ.get(ENV_TRANSCRIPTS_ROOT, ""),
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
            "\n# Reconciliation-loop seams.\n"
            f'hub_url = "{self.hub_url}"\n'
            "\n# Names the env var carrying this runner's hub bearer token (issue #86b);\n"
            "# the secret itself lives in the runtime env file, never here.\n"
            f'token_env = "{self.token_env}"\n'
            f'runner_id = "{self.runner_id}"\n'
            f'workspace_id = "{self.workspace_id}"\n'
            f'workspace_root = "{self.workspace_root}"\n'
            f"workspace_envs = [{envs}]\n"
            f'harness_binary = "{self.harness_binary}"\n'
            f'harness_permission_mode = "{self.harness_permission_mode or ""}"\n'
            f"worker_settings_path = {settings}\n"
            f"max_agents = {self.max_agents}\n"
            f'base_branch = "{self.base_branch}"\n'
            "\n# Human gates this runner imposes by node name; empty = none.\n"
            f"gates = [{gates}]\n"
            "\n# The runner-owned workspace prompt prepended to every worker spawn (issue #17).\n"
            "# `workspace_prompt` is inline text; `workspace_prompt_file` (a path) wins when set.\n"
            "# Empty = table-only injection. Replace at runtime via PUT /api/workspace-prompt.\n"
            f"workspace_prompt = {workspace_prompt}\n"
            f"workspace_prompt_file = {workspace_prompt_file}\n"
            "\n# Where the coding harness writes session transcripts (issue #29);\n"
            "# empty = ~/.claude/projects.\n"
            f'transcripts_root = "{self.transcripts_root}"\n'
            "\n# Spend controls (epic #57); absent = no cap. `chunk_cap_usd` parks a chunk\n"
            "# needs_human at its next step boundary once its derived spend reaches this cap.\n"
            "# `runner_ceiling_usd` engages this runner's own local pause brake (the same one\n"
            "# `blizzard runner pause` sets) once its rolling `window_hours`-long spend reaches\n"
            "# this value; `blizzard runner start` is the only clear — it does not lift itself\n"
            "# when the window later rolls the spend back under the ceiling.\n"
            "[cost]\n"
            + (
                f"chunk_cap_usd = {self.chunk_cap_usd}\n"
                if self.chunk_cap_usd is not None
                else "# chunk_cap_usd = 5.0\n"
            )
            + (
                f"runner_ceiling_usd = {self.runner_ceiling_usd}\n"
                if self.runner_ceiling_usd is not None
                else "# runner_ceiling_usd = 50.0\n"
            )
            + f"window_hours = {self.runner_ceiling_window_hours}\n"
            + "\n# The worker spawn-environment allowlist's operator extension (`bzh:worker-env-allowlist`).\n"
            + "# The base allowlist (PATH/HOME/USER/LANG/LC_*/TERM/TMPDIR) always reaches a worker;\n"
            + "# name additional vars here to forward them too. Empty = base allowlist only. The\n"
            + "# BLIZZARD_* identity vars are injected per spawn/judge/resume, not passed through.\n"
            + "[worker]\n"
            + f"env_passthrough = [{', '.join(f'"{v}"' for v in self.worker_env_passthrough)}]\n"
        )

    @classmethod
    def load(cls, root: Path, *, host: str | None = None, port: int | None = None) -> RunnerConfig:
        """Read a runtime root's config file; overlay CLI host/port when given."""
        root = root.resolve()
        path = root / CONFIG_FILENAME
        if not path.exists():
            raise ConfigError(f"{root} is not an initialized runner runtime (run `blizzard runner init {root}`)")
        raw = tomllib.loads(path.read_text())
        token_env = str(raw.get("token_env", DEFAULT_TOKEN_ENV))
        return cls(
            root=root,
            db_url=str(raw["db_url"]),
            host=host or str(raw.get("host", DEFAULT_HOST)),
            port=port if port is not None else int(raw.get("port", DEFAULT_PORT)),
            hub_url=str(raw.get("hub_url", DEFAULT_HUB_URL)),
            token_env=token_env,
            hub_token=os.environ.get(token_env, ""),
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
            transcripts_root=str(raw.get("transcripts_root", "")),
            chunk_cap_usd=_parse_chunk_cap_usd(raw.get("cost", {})),
            runner_ceiling_usd=_parse_runner_ceiling_usd(raw.get("cost", {})),
            runner_ceiling_window_hours=_parse_runner_ceiling_window_hours(raw.get("cost", {})),
            worker_env_passthrough=_parse_worker_env_passthrough(raw.get("worker", {})),
        )


def _as_env_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return DEFAULT_ENV_POOL


def _parse_chunk_cap_usd(cost: object) -> float | None:
    """``[cost].chunk_cap_usd`` — absent (the table, or just this key) means no cap."""
    if not isinstance(cost, dict) or cost.get("chunk_cap_usd") is None:
        return None
    return float(cost["chunk_cap_usd"])


def _parse_runner_ceiling_usd(cost: object) -> float | None:
    """``[cost].runner_ceiling_usd`` (issue #61b) — absent (the table, or just this key)
    means no ceiling, mirroring :func:`_parse_chunk_cap_usd`'s shape exactly."""
    if not isinstance(cost, dict) or cost.get("runner_ceiling_usd") is None:
        return None
    return float(cost["runner_ceiling_usd"])


def _parse_runner_ceiling_window_hours(cost: object) -> float:
    """``[cost].window_hours`` (issue #61b) — defaults to
    :data:`DEFAULT_RUNNER_CEILING_WINDOW_HOURS` when absent (whether or not a ceiling is
    set alongside it); meaningless while ``runner_ceiling_usd`` is ``None``."""
    if not isinstance(cost, dict) or cost.get("window_hours") is None:
        return DEFAULT_RUNNER_CEILING_WINDOW_HOURS
    return float(cost["window_hours"])


def _parse_worker_env_passthrough(worker: object) -> tuple[str, ...]:
    """``[worker].env_passthrough`` (issue #88) — absent (the table, or just this key)
    means no operator extension, mirroring :func:`_parse_chunk_cap_usd`'s shape."""
    if not isinstance(worker, dict) or worker.get("env_passthrough") is None:
        return ()
    return tuple(str(v) for v in worker["env_passthrough"])
