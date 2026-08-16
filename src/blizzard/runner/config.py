"""Runner runtime configuration — resolved from a runtime directory.

``blizzard runner init <dir>`` scaffolds a config file and a data directory; the daemon and
the offline ``migrate`` verb read it back. The store URL is the single portability knob
(``bzh:sql-portable``), defaulting to embedded sqlite."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.public_origins import PublicOrigins
from blizzard.runner.transcripts.caps import CHUNK_TRANSCRIPT_MAX_BYTES, TRANSCRIPT_RECORD_MAX_BYTES

CONFIG_FILENAME = "blizzard-runner.toml"
DATA_DIRNAME = "data"
# The runner-owned worker hook file `init` scaffolds, delivering the heartbeat hook.
WORKER_SETTINGS_FILENAME = "worker-settings.json"
# The local API's unix socket, under the state dir beside the store; filesystem
# permissions are its access control.
SOCKET_FILENAME = "runner.sock"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8431

ENV_HOST = "BZ_RUNNER_HOST"
ENV_PORT = "BZ_RUNNER_PORT"
ENV_HUB_URL = "BZ_HUB_URL"
# Injected per feature env, so a fresh `runner init` scaffolds a runnable config with no
# hand-editing of the toml.
ENV_WORKSPACE_ROOT = "BZ_WORKSPACE_ROOT"
ENV_WORKSPACE_ENVS = "BZ_WORKSPACE_ENVS"  # comma-separated env-id pool
ENV_HARNESS_BINARY = "BZ_HARNESS_BINARY"
ENV_HARNESS_PERMISSION_MODE = "BZ_HARNESS_PERMISSION_MODE"
ENV_BASE_BRANCH = "BZ_BASE_BRANCH"
ENV_GATES = "BZ_RUNNER_GATES"  # comma-separated node names this runner gates
ENV_WORKSPACE_PROMPT = "BZ_WORKSPACE_PROMPT"  # the runner-owned workspace prompt, inline (issue #17)
ENV_RUNNER_PROMPT = "BZ_RUNNER_PROMPT"  # the blizzard-preamble override, inline (issue #103)
# Where the harness writes session transcripts (issue #29); empty resolves to a default at
# the composition root, never here.
ENV_TRANSCRIPTS_ROOT = "BZ_TRANSCRIPTS_ROOT"
# The browser-reachable base URLs this runner answers on (issues #95, #287), comma-separated.
ENV_PUBLIC_URL = "BZ_RUNNER_PUBLIC_URL"

# The federation callback route, owned here so the registered URI set and the URL the bounce
# presents cannot drift; `runner.auth.federation` imports it rather than restating the literal.
CALLBACK_PATH = "/api/auth/callback"

# Reconciliation-loop defaults — the runner is machine-level and single-workspace.
DEFAULT_HUB_URL = "http://127.0.0.1:8421"  # the hub's default bind (band +2)
DEFAULT_RUNNER_ID = "runner-local"
DEFAULT_WORKSPACE_ID = "workspace-local"
DEFAULT_HARNESS_BINARY = "claude"
# A headless worker has no one to approve tool use, so it needs a non-interactive mode;
# a config may set this empty to omit the flag.
DEFAULT_HARNESS_PERMISSION_MODE = "bypassPermissions"
DEFAULT_MAX_AGENTS = 1
DEFAULT_BASE_BRANCH = "main"
# The env var NAMING this runner's hub bearer token (issue #86b) — the toml round-trips the
# variable name only, never the secret.
DEFAULT_TOKEN_ENV = "BZ_HUB_TOKEN"
DEFAULT_ENV_POOL: tuple[str, ...] = ("e1",)
# The runner-ceiling rolling window's default length (issue #61b) — a ceiling with no
# declared window still needs one to sum over.
DEFAULT_RUNNER_CEILING_WINDOW_HOURS = 24.0
# How often the tick re-samples the harness's rate-limit windows (issue #218) — a
# diagnostic, best-effort read, not a spend control.
DEFAULT_EXTERNAL_USAGE_SAMPLE_INTERVAL_SECONDS = 300
# Well under the minutes a context takes to move; each read is a bounded tail read.
DEFAULT_CONTEXT_SAMPLE_INTERVAL_SECONDS = 60
DEFAULT_AUTH_HUB_ROLE = "mirror"


class ConfigError(RuntimeError):
    """A runtime directory is missing its config — it was never initialized."""


def _cap_line(key: str, value: int | None, default: int) -> str:
    """One ``[transcripts]`` ceiling: live once overridden, commented at its default so the
    scaffolded file always shows an operator what the ceiling IS (blizzard#338)."""
    return f"{key} = {value}\n" if value is not None else f"# {key} = {default}\n"


@dataclass(frozen=True)
class Table:
    """One parsed toml table, read through the coercions the config fields share.

    A value that is not a table reads as empty, so an absent section and an absent key
    behave alike."""

    body: dict[str, Any]

    @classmethod
    def of(cls, value: object) -> Table:
        return cls(value if isinstance(value, dict) else {})

    def text(self, key: str) -> str | None:
        value = self.body.get(key)
        return None if value is None else str(value)

    def word(self, key: str) -> str | None:
        """A string read whose empty value counts as absent."""
        value = self.body.get(key)
        return str(value) if value else None

    def real(self, key: str) -> float | None:
        value = self.body.get(key)
        return None if value is None else float(value)

    def count(self, key: str, default: int) -> int:
        value = self.body.get(key)
        return default if value is None else int(value)

    def boolean(self, key: str, default: bool) -> bool:
        """A real TOML boolean, or ``default`` when ``key`` is absent. Raises on anything
        else (blizzard#246): ``bool()`` on a non-empty string is truthy regardless of its
        text, so a typo'd ``ship = "false"`` must never silently turn a switch on."""
        value = self.body.get(key)
        if value is None:
            return default
        if not isinstance(value, bool):
            raise ConfigError(f"{key!r} must be a boolean, got {value!r}")
        return value

    def names(self, key: str) -> tuple[str, ...]:
        """Every entry at ``key`` as a string; an absent key is empty."""
        value = self.body.get(key)
        return () if value is None else tuple(str(entry) for entry in value)

    def listed(self, key: str, default: tuple[str, ...]) -> tuple[str, ...]:
        """Every entry at ``key`` as a string, falling back to ``default`` unless it is a list."""
        value = self.body.get(key)
        if not isinstance(value, (list, tuple)):
            return default
        return tuple(str(entry) for entry in value)

    def pairs(self, key: str) -> tuple[tuple[str, str], ...]:
        """The nested table at ``key`` as key/value pairs — a frozen dataclass field must
        stay hashable. Absent, or present but empty, means none."""
        nested = self.body.get(key)
        if not isinstance(nested, dict):
            return ()
        return tuple((str(name), str(value)) for name, value in nested.items())


@dataclass(frozen=True)
class Spend:
    """The ``[cost]`` table's spend controls (epic #57) — absent means uncapped."""

    table: Table

    @classmethod
    def of(cls, raw: object) -> Spend:
        return cls(Table.of(raw))

    @property
    def chunk_cap_usd(self) -> float | None:
        return self.table.real("chunk_cap_usd")

    @property
    def ceiling_usd(self) -> float | None:
        return self.table.real("runner_ceiling_usd")

    @property
    def window_hours(self) -> float:
        """Defaulted whether or not a ceiling is set alongside it."""
        hours = self.table.real("window_hours")
        return DEFAULT_RUNNER_CEILING_WINDOW_HOURS if hours is None else hours


@dataclass(frozen=True)
class Context:
    """The ``[context]`` table — the live session-context warn lane.

    Config rather than graph content on purpose: this observes, a graph's ``rotate`` block
    decides. So the line is re-aimed without re-minting every graph declaring a bound."""

    table: Table

    @classmethod
    def of(cls, raw: object) -> Context:
        return cls(Table.of(raw))

    @property
    def warn_tokens(self) -> int | None:
        """The line a running session's context is warned about crossing; absent = no lane,
        and nothing is sampled at all."""
        return self.table.count("warn_tokens", 0) or None

    @property
    def sample_interval_seconds(self) -> int:
        return self.table.count("sample_interval_seconds", DEFAULT_CONTEXT_SAMPLE_INTERVAL_SECONDS)


@dataclass(frozen=True)
class ExternalUsage:
    """The ``[external_subscription_usage]`` table (issue #218)."""

    table: Table

    @classmethod
    def of(cls, raw: object) -> ExternalUsage:
        return cls(Table.of(raw))

    @property
    def sample_interval_seconds(self) -> int:
        return self.table.count("sample_interval_seconds", DEFAULT_EXTERNAL_USAGE_SAMPLE_INTERVAL_SECONDS)

    @property
    def credentials_path(self) -> str | None:
        return self.table.text("credentials_path")


@dataclass(frozen=True)
class Transcripts:
    """The ``[transcripts]`` table (issue #246) — the dedicated outbound lane's own switch,
    distinct from the top-level ``transcripts_root`` (the harness source's read location)."""

    table: Table

    @classmethod
    def of(cls, raw: object) -> Transcripts:
        return cls(Table.of(raw))

    @property
    def ship(self) -> bool:
        """Off by default (D5) — a rollout decision, not a discard-sink one:
        ``#247`` already landed the hub's durable, compressed-at-rest, operator-gated
        segment store, so a `True` value here would be retained, not wasted bandwidth.
        The dogfood fleet just has not turned shipping on yet."""
        return self.table.boolean("ship", False)

    @property
    def record_max_bytes(self) -> int | None:
        """Override for the pump's own per-record cap; ``None`` keeps its default
        (blizzard#338). Must stay at or below the hub's `record_max_bytes` — the ordering
        and its consequence are at :mod:`blizzard.runner.transcripts.caps`."""
        return self._cap("record_max_bytes")

    @property
    def chunk_max_bytes(self) -> int | None:
        """Override for the pump's own per-chunk budget; ``None`` keeps its default
        (blizzard#338)."""
        return self._cap("chunk_max_bytes")

    def _cap(self, key: str) -> int | None:
        value = self.table.body.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"transcripts.{key} must be an integer number of bytes, got {value!r}")
        if value <= 0:
            # Zero reads as "unset" while rejecting every record; a cap has no "off" value.
            raise ConfigError(f"transcripts.{key} must be positive, got {value!r}")
        return value


@dataclass(frozen=True)
class Auth:
    """The ``[auth]`` table (issue #95) — runner-local role resolution, keyed by hub username."""

    table: Table

    @classmethod
    def of(cls, raw: object) -> Auth:
        return cls(Table.of(raw))

    @property
    def superuser(self) -> str | None:
        return self.table.word("superuser")

    @property
    def hub_role_default(self) -> str:
        return self.table.word("hub_role_default") or DEFAULT_AUTH_HUB_ROLE

    @property
    def users(self) -> tuple[tuple[str, str], ...]:
        return self.table.pairs("users")


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
    #: Names the env var carrying the hub bearer token (issue #86b); :attr:`hub_token` is
    #: the resolved secret, and empty is a valid state.
    token_env: str = DEFAULT_TOKEN_ENV
    hub_token: str = ""
    workspace_root: str = ""  # the winter workspace the provider drives; required to FILL
    workspace_envs: tuple[str, ...] = DEFAULT_ENV_POOL  # the provider's static env pool
    harness_binary: str = DEFAULT_HARNESS_BINARY  # mock-claude-code in tests, `claude` in prod
    harness_permission_mode: str | None = None  # `claude -p --permission-mode` (headless); None omits it
    worker_settings_path: str | None = None  # the runner-owned worker hook file (P7)
    max_agents: int = DEFAULT_MAX_AGENTS
    base_branch: str = DEFAULT_BASE_BRANCH
    #: Node NAMES this runner imposes a human gate on; reloaded every tick.
    gates: tuple[str, ...] = ()
    #: The workspace prompt prepended to a worker spawn (issue #17) — two source knobs,
    #: one effective value (:meth:`resolved_workspace_prompt`); the file wins when set.
    workspace_prompt: str = ""
    workspace_prompt_file: str = ""
    #: The override of the baked-in blizzard preamble (issue #103), prepended ahead of
    #: :attr:`workspace_prompt`; empty resolves to the baked default.
    runner_prompt: str = ""
    runner_prompt_file: str = ""
    #: Where the harness writes session transcripts (issue #29); read from the toml, never
    #: re-read from the environment live, so a changed env var needs a re-``init``.
    transcripts_root: str = ""
    #: Per-record cap and per-chunk budget overrides (``[transcripts]``, blizzard#338);
    #: ``None`` keeps `transcript_pump`'s own defaults, which own the values.
    transcript_record_max_bytes: int | None = None
    transcript_chunk_max_bytes: int | None = None
    #: The transcript outbound lane's own switch (``[transcripts] ship``, issue #246);
    #: off by default (D5) — the pump enqueues no delta while this is ``False``.
    transcripts_ship: bool = False
    #: The per-chunk spend cap (issue #61a); ``None`` means no cap. A chunk reaching it
    #: parks ``needs_human`` at its next step boundary.
    chunk_cap_usd: float | None = None
    #: The runner-wide spend ceiling (issue #61b); ``None`` means none. Crossing it engages
    #: the local pause brake, and there is no auto-unpause once the window drops back under.
    runner_ceiling_usd: float | None = None
    #: The runner ceiling's rolling window length in hours (issue #61b) — unused while
    #: :attr:`runner_ceiling_usd` is ``None``.
    runner_ceiling_window_hours: float = DEFAULT_RUNNER_CEILING_WINDOW_HOURS
    #: The external-usage sample cadence in seconds (issue #218) — a diagnostic cadence,
    #: not a spend control, so absent means the default rather than never.
    external_usage_sample_interval_seconds: int = DEFAULT_EXTERNAL_USAGE_SAMPLE_INTERVAL_SECONDS
    #: An override for the credential file the external-usage sampler reads (issue #218);
    #: ``None`` means the adapter's own default.
    external_usage_credentials_path: str | None = None
    #: The session-context warn line; ``None`` disables the lane — nothing is sampled, nothing gated.
    context_warn_tokens: int | None = None
    #: How often a running lease's context is re-read — unused while the lane is off.
    context_sample_interval_seconds: int = DEFAULT_CONTEXT_SAMPLE_INTERVAL_SECONDS
    #: The declared extension to the worker spawn-environment allowlist (issue #88) — a
    #: worker's env is that allowlist, never a full ``os.environ`` copy.
    worker_env_passthrough: tuple[str, ...] = ()
    #: Every browser-reachable origin this runner answers on, authored as `public_url` — one URL or
    #: a list; first is canonical, empty registers no federation identity (issues #95, #287).
    public_urls: tuple[str, ...] = ()
    #: The hub username naming this runner's own sovereign (issue #95) — config-only,
    #: never assignable through a JWT claim.
    auth_superuser: str | None = None
    #: The fallback role for a hub identity with no `[auth.users]` override (issue #95) —
    #: `"mirror"` reproduces the hub's claim, a fixed role floors every unmatched identity.
    auth_hub_role_default: str = DEFAULT_AUTH_HUB_ROLE
    #: Per-username role overrides (issue #95), keyed on the JWT's `username` claim only,
    #: never `email`, which is mutable and may be null.
    auth_users: tuple[tuple[str, str], ...] = ()
    #: Model tier-alias mappings (issue #144) onto the names *this* runner's harness
    #: understands; an alias mapped by neither this nor the adapter is skipped, never fatal.
    model_aliases: tuple[tuple[str, str], ...] = ()
    #: Effort alias mappings (issue #144) onto the `low|medium|high|max` ordinal; the
    #: well-known four need no entry.
    effort_aliases: tuple[tuple[str, str], ...] = ()
    #: The reverse-proxy trust set (issue #130) — addresses or CIDRs whose
    #: `X-Forwarded-Proto` is honored; empty ignores the header from every peer.
    trusted_proxies: tuple[str, ...] = ()

    @property
    def public_origins(self) -> PublicOrigins:
        """Every origin this runner answers on (issue #287), in declaration order."""
        return PublicOrigins.of(*self.public_urls)

    @property
    def public_url(self) -> str:
        """The canonical origin — the first declared. It is what the hub records as this runner's own
        URL, and what a request whose ``Host`` matches no declared origin falls back to. Empty when
        none is declared, which is how a runner registers no federation identity at all."""
        return self.public_origins.canonical or ""

    @property
    def redirect_uris(self) -> tuple[str, ...]:
        """The redirect URIs this runner presents to the hub's IdP authorize endpoint (issue #95) — one
        per declared origin (issue #287), derived from :attr:`public_origins`, never independently
        configured. The hub exact-matches a presented URI against this registered set, so an origin
        missing from it cannot complete a bounce."""
        return self.public_origins.callback_uris(CALLBACK_PATH)

    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_FILENAME

    @property
    def data_dir(self) -> Path:
        return self.root / DATA_DIRNAME

    @property
    def socket_path(self) -> Path:
        return self.socket_path_for(self.root)

    @staticmethod
    def socket_path_for(root: Path) -> Path:
        """The local API's unix socket, under the state dir with the store — derivable from
        the path alone, so a local verb can address the daemon from ``--dir`` without
        reading the toml or opening the store."""
        return root / SOCKET_FILENAME

    @property
    def local_api_url(self) -> str:
        """The runner's own TCP door — the one derivation of the ``BLIZZARD_RUNNER_URL``
        a worker or taken-over session is handed, shared by the loop context and the
        takeover service so the two cannot drift."""
        return f"http://{self.host}:{self.port}"

    @staticmethod
    def default_db_url(root: Path) -> str:
        return f"sqlite:///{(root / DATA_DIRNAME / 'runner.db').resolve()}"

    def resolved_workspace_prompt(self) -> str:
        """The effective static workspace prompt (issue #17), resolved from its two knobs.

        ``workspace_prompt_file`` wins when set, resolving a relative path under
        :attr:`root`. A configured-but-missing file raises, which is not the same as an
        *absent* prompt: both knobs empty is valid and returns ``""``."""
        if self.workspace_prompt_file:
            path = Path(self.workspace_prompt_file)
            if not path.is_absolute():
                path = self.root / path
            if not path.exists():
                raise ConfigError(f"workspace_prompt_file does not exist: {path}")
            return path.read_text()
        return self.workspace_prompt

    def resolved_runner_prompt(self) -> str:
        """The effective override for the blizzard preamble (issue #103), from its two knobs.

        Mirrors :meth:`resolved_workspace_prompt`: the file knob wins when set, and a
        configured-but-missing file raises. Both empty returns ``""``, which the preamble
        renderer reads as "use the baked default", never as an absent layer."""
        if self.runner_prompt_file:
            path = Path(self.runner_prompt_file)
            if not path.is_absolute():
                path = self.root / path
            if not path.exists():
                raise ConfigError(f"runner_prompt_file does not exist: {path}")
            return path.read_text()
        return self.runner_prompt

    def auth_headers(self) -> dict[str, str]:
        """The outbound ``Authorization`` header every runner->hub call carries (issue #86b).

        One credential path for every outbound call rather than a header built per call
        site. Empty when :attr:`hub_token` is unset: an unenrolled runner attaches
        nothing, and the hub decides whether that is tolerated."""
        if not self.hub_token:
            return {}
        return {"Authorization": f"Bearer {self.hub_token}"}

    @classmethod
    def scaffold(cls, root: Path) -> RunnerConfig:
        """The default config for a fresh runtime root (used by ``init``).

        The loop seams are read from the injected environment when present, so ``init``
        produces a runnable config; each falls back to its dataclass default."""
        envs = os.environ.get(ENV_WORKSPACE_ENVS)
        gates = os.environ.get(ENV_GATES)
        public_urls = os.environ.get(ENV_PUBLIC_URL, "")
        return cls(
            root=root,
            db_url=cls.default_db_url(root),
            host=os.environ.get(ENV_HOST, DEFAULT_HOST),
            port=int(os.environ.get(ENV_PORT, DEFAULT_PORT)),
            hub_url=os.environ.get(ENV_HUB_URL, DEFAULT_HUB_URL),
            token_env=DEFAULT_TOKEN_ENV,
            hub_token=os.environ.get(DEFAULT_TOKEN_ENV, ""),
            workspace_root=os.environ.get(ENV_WORKSPACE_ROOT, ""),
            workspace_envs=tuple(e.strip() for e in envs.split(",") if e.strip()) if envs else DEFAULT_ENV_POOL,
            harness_binary=os.environ.get(ENV_HARNESS_BINARY, DEFAULT_HARNESS_BINARY),
            harness_permission_mode=os.environ.get(ENV_HARNESS_PERMISSION_MODE, DEFAULT_HARNESS_PERMISSION_MODE)
            or None,
            base_branch=os.environ.get(ENV_BASE_BRANCH, DEFAULT_BASE_BRANCH),
            gates=tuple(g.strip() for g in gates.split(",") if g.strip()) if gates else (),
            # The worker hook file `init` writes alongside the config; the adapter
            # delivers it as `--settings` so a spawned worker heartbeats.
            worker_settings_path=str(root / WORKER_SETTINGS_FILENAME),
            # Empty on a fresh scaffold; seeded from the environment so `init` can inject
            # a default without hand-editing (issue #17).
            workspace_prompt=os.environ.get(ENV_WORKSPACE_PROMPT, ""),
            # Empty on a fresh scaffold means the baked-in preamble is used (issue #103).
            runner_prompt=os.environ.get(ENV_RUNNER_PROMPT, ""),
            transcripts_root=os.environ.get(ENV_TRANSCRIPTS_ROOT, ""),
            public_urls=PublicOrigins.entries(public_urls.split(","), ConfigError),
        )

    def to_toml(self) -> str:
        envs = ", ".join(f'"{e}"' for e in self.workspace_envs)
        gates = ", ".join(f'"{g}"' for g in self.gates)
        # A lone origin stays a bare string, the shape the common single-origin deployment authors;
        # several become a list. Both round-trip back through `load`.
        public_url = (
            "[" + ", ".join(f'"{u}"' for u in self.public_urls) + "]"
            if len(self.public_urls) > 1
            else f'"{self.public_url}"'
        )
        settings = f'"{self.worker_settings_path}"' if self.worker_settings_path else '""'
        # `json.dumps` emits a valid TOML basic string: TOML shares JSON's escapes
        # (\n, \t, \", \\, \uXXXX), so a multi-line inline prompt round-trips intact.
        workspace_prompt = json.dumps(self.workspace_prompt)
        workspace_prompt_file = json.dumps(self.workspace_prompt_file)
        runner_prompt = json.dumps(self.runner_prompt)
        runner_prompt_file = json.dumps(self.runner_prompt_file)
        return (
            "# blizzard-runner runtime configuration (blizzard runner init)\n"
            f'db_url = "{self.db_url}"\n'
            f'host = "{self.host}"\n'
            f"port = {self.port}\n"
            "\n# Reconciliation-loop seams.\n"
            f'hub_url = "{self.hub_url}"\n'
            "\n# The browser-reachable origins this runner answers on — one bare origin, or a list.\n"
            "# Empty registers no federation identity, so the human web surface stays unreachable.\n"
            "# The browser follows these, so a loopback-only value answers on this host alone; a\n"
            "# non-loopback origin must be https fronted by a proxy. First is canonical. See the\n"
            '# "Runner-side federation" section of the deployment guide before changing this.\n'
            f"public_url = {public_url}\n"
            "\n# Reverse-proxy trust set (issue #130): proxy IPs/CIDRs whose X-Forwarded-Proto is\n"
            "# honored when minting the SSO session cookie's Secure flag. Empty = header ignored.\n"
            "# Required for any https origin above, and the proxy must also pass the browser's\n"
            "# original Host through — selection reads it, and nginx replaces it by default.\n"
            f"trusted_proxies = [{', '.join(f'"{p}"' for p in self.trusted_proxies)}]\n"
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
            "\n# The runner-owned workspace prompt prepended to a worker spawn (issue #17).\n"
            "# `workspace_prompt` is inline text; `workspace_prompt_file` (a path) wins when set.\n"
            "# Empty = table-only injection. Replace at runtime via PUT /api/workspace-prompt.\n"
            "# A resumed spawn re-sends this only when it changed, announced as updated.\n"
            f"workspace_prompt = {workspace_prompt}\n"
            f"workspace_prompt_file = {workspace_prompt_file}\n"
            "\n# The operator's override of the baked-in blizzard preamble (issue #103) — layer 1\n"
            "# of the spawn preamble, ahead of `workspace_prompt` above. `runner_prompt` is inline\n"
            "# text; `runner_prompt_file` (a path) wins when set. Empty = the baked default\n"
            "# (DEFAULT_BLIZZARD_PREAMBLE) is used instead; config/startup only, no runtime override.\n"
            f"runner_prompt = {runner_prompt}\n"
            f"runner_prompt_file = {runner_prompt_file}\n"
            "\n# Where the coding harness writes session transcripts (issue #29);\n"
            "# empty = ~/.claude/projects.\n"
            f'transcripts_root = "{self.transcripts_root}"\n'
            "\n# The transcript outbound lane (issue #246) — off by default; the hub's own\n"
            "# durable, compressed-at-rest segment store (issue #247) is already landed, so\n"
            "# turning this on is a rollout decision, not a bandwidth-for-nothing one.\n"
            "[transcripts]\n"
            f"ship = {'true' if self.transcripts_ship else 'false'}\n"
            "# This lane's own byte ceilings (blizzard#338), shown at their defaults;\n"
            "# uncomment to override. Widen `chunk_max_bytes` for a backfill window — a\n"
            "# `blizzard runner transcript reship` spends that budget a SECOND time over the\n"
            "# same chunk — then restore it. Keep `record_max_bytes` at or BELOW the hub's own\n"
            "# `record_max_bytes`: over the hub's, a record loses its turns whole; over this\n"
            "# one, the pump merely shrinks them.\n"
            + _cap_line("record_max_bytes", self.transcript_record_max_bytes, TRANSCRIPT_RECORD_MAX_BYTES)
            + _cap_line("chunk_max_bytes", self.transcript_chunk_max_bytes, CHUNK_TRANSCRIPT_MAX_BYTES)
            + "\n# Spend controls (epic #57); absent = no cap. `chunk_cap_usd` parks a chunk\n"
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
            + (
                "\n# The live session-context warn lane; absent = off, and nothing is sampled.\n"
                "# `warn_tokens` is the context a RUNNING worker's session is warned about\n"
                "# crossing — observation only, distinct from a graph's own `rotate` bounds,\n"
                "# which decide whether the NEXT node-step resumes that session at all.\n"
                "[context]\n"
            )
            + (
                f"warn_tokens = {self.context_warn_tokens}\n"
                if self.context_warn_tokens is not None
                else "# warn_tokens = 300000\n"
            )
            + f"sample_interval_seconds = {self.context_sample_interval_seconds}\n"
            + "\n# How often (seconds) the tick re-samples the harness's own subscription rate-limit\n"
            + "# windows (issue #218) — a diagnostic, best-effort read, not a spend control.\n"
            + "[external_subscription_usage]\n"
            + f"sample_interval_seconds = {self.external_usage_sample_interval_seconds}\n"
            + (
                f'credentials_path = "{self.external_usage_credentials_path}"\n'
                if self.external_usage_credentials_path is not None
                else '# credentials_path = "/path/to/.credentials.json"  # defaults to ~/.claude/.credentials.json\n'
            )
            + "\n# The worker spawn-environment allowlist's operator extension (`bzh:worker-env-allowlist`).\n"
            + "# The base allowlist (PATH/HOME/USER/LANG/LC_*/TERM/TMPDIR) always reaches a worker;\n"
            + "# name additional vars here to forward them too. Empty = base allowlist only. The\n"
            + "# BLIZZARD_* identity vars are injected per spawn/judge/resume, not passed through.\n"
            + "[worker]\n"
            + f"env_passthrough = [{', '.join(f'"{v}"' for v in self.worker_env_passthrough)}]\n"
            + "\n# Runner-local role resolution, keyed by hub username (issue #95) — lives only here,\n"
            + '# never in the hub store/admin page. `hub_role_default` is "mirror" or a fixed cap\n'
            + '# ("contributor"/"guest"/"pending"); `superuser` names this runner\'s own sovereign.\n'
            + "[auth]\n"
            + (f'superuser = "{self.auth_superuser}"\n' if self.auth_superuser else '# superuser = "<hub-username>"\n')
            + f'hub_role_default = "{self.auth_hub_role_default}"\n'
            + "\n[auth.users]\n"
            + "".join(f'{username} = "{role}"\n' for username, role in self.auth_users)
            + "\n# Model and effort tier aliases (issue #144) — how THIS runner's harness resolves the\n"
            + "# harness-agnostic names a graph's `sessions:` declaration (or a chunk default) uses.\n"
            + "# The Claude Code adapter ships built-in defaults for the three standard tiers\n"
            + "# (blizzard:frontier/advanced/basic), so a zero-config runner needs no entry here;\n"
            + "# an entry overrides the built-in. An unmapped alias is skipped at resolution, never\n"
            + "# a spawn failure. Effort maps onto the low|medium|high|max ordinal.\n"
            + "[models.aliases]\n"
            + "".join(f'"{alias}" = "{native}"\n' for alias, native in self.model_aliases)
            + "\n[effort.aliases]\n"
            + "".join(f'"{alias}" = "{native}"\n' for alias, native in self.effort_aliases)
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
        spend = Spend.of(raw.get("cost"))
        usage = ExternalUsage.of(raw.get("external_subscription_usage"))
        context = Context.of(raw.get("context"))
        auth = Auth.of(raw.get("auth"))
        transcripts = Transcripts.of(raw.get("transcripts"))
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
            workspace_envs=Table.of(raw).listed("workspace_envs", DEFAULT_ENV_POOL),
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
            runner_prompt=str(raw.get("runner_prompt", "")),
            runner_prompt_file=str(raw.get("runner_prompt_file", "")),
            transcripts_root=str(raw.get("transcripts_root", "")),
            transcripts_ship=transcripts.ship,
            transcript_record_max_bytes=transcripts.record_max_bytes,
            transcript_chunk_max_bytes=transcripts.chunk_max_bytes,
            chunk_cap_usd=spend.chunk_cap_usd,
            runner_ceiling_usd=spend.ceiling_usd,
            runner_ceiling_window_hours=spend.window_hours,
            external_usage_sample_interval_seconds=usage.sample_interval_seconds,
            external_usage_credentials_path=usage.credentials_path,
            context_warn_tokens=context.warn_tokens,
            context_sample_interval_seconds=context.sample_interval_seconds,
            worker_env_passthrough=Table.of(raw.get("worker")).names("env_passthrough"),
            public_urls=PublicOrigins.entries(raw.get("public_url"), ConfigError),
            auth_superuser=auth.superuser,
            auth_hub_role_default=auth.hub_role_default,
            auth_users=auth.users,
            model_aliases=Table.of(raw.get("models")).pairs("aliases"),
            effort_aliases=Table.of(raw.get("effort")).pairs("aliases"),
            trusted_proxies=TrustedProxies.entries(raw.get("trusted_proxies"), ConfigError),
        )
