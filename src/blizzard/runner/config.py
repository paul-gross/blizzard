"""Runner runtime configuration — resolved from a runtime directory.

``blizzard runner init <dir>`` scaffolds a config file and a data directory; the daemon and
the offline ``migrate`` verb read it back. The store URL is the single portability knob
(``bzh:sql-portable``), defaulting to embedded sqlite."""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.public_origins import PublicOrigins
from blizzard.foundation.roles import domain_model
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.runner.auth.roles import RolePolicy
from blizzard.runner.auth.session import CALLBACK_PATH
from blizzard.runner.config_table import ConfigError, Table
from blizzard.runner.environments.factory import WORKSPACE_PROVIDERS, WorkspaceSettings
from blizzard.runner.environments.provider import WorkspaceRepo
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.bundle import HARNESS_CONFIG_DIRNAME
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.wiring import HarnessSections, HarnessSettings
from blizzard.runner.harness.workspace_prompts import PACKAGED, UnknownWorkspacePromptSample
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC
from blizzard.runner.transcripts.caps import CHUNK_TRANSCRIPT_MAX_BYTES, TRANSCRIPT_RECORD_MAX_BYTES

# Default declaration synthesized from the retained `[external_subscription_usage]` configuration.
LEGACY_ANTHROPIC_SLUG = "anthropic"
LEGACY_ANTHROPIC_NAME = "Anthropic"

#: Winter's own exporter variables; only the runner sets them, and only under `worker_programs`.
_WINTER_OTEL_PREFIX = "WINTER_OTEL_"

CONFIG_FILENAME = "blizzard-runner.toml"
DATA_DIRNAME = "data"
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
ENV_BASE_BRANCH = "BZ_BASE_BRANCH"
ENV_GATES = "BZ_RUNNER_GATES"  # comma-separated node names this runner gates
ENV_WORKSPACE_PROMPT = "BZ_WORKSPACE_PROMPT"  # the runner-owned workspace prompt, inline
ENV_WORKSPACE_PROMPT_PACKAGE = "BZ_WORKSPACE_PROMPT_PACKAGE"  # a packaged workspace-prompt sample, by name (#344)
ENV_RUNNER_PROMPT = "BZ_RUNNER_PROMPT"  # the blizzard-preamble override, inline
# Where the harness writes session transcripts; empty resolves to a default at
# the composition root, never here.
ENV_TRANSCRIPTS_ROOT = "BZ_TRANSCRIPTS_ROOT"
# The browser-reachable base URLs this runner answers on, comma-separated.
ENV_PUBLIC_URL = "BZ_RUNNER_PUBLIC_URL"

# Reconciliation-loop defaults — the runner is machine-level and single-workspace.
DEFAULT_HUB_URL = "http://127.0.0.1:8421"  # the hub's default bind (band +2)
DEFAULT_RUNNER_ID = "runner-local"
DEFAULT_WORKSPACE_ID = "workspace-local"
DEFAULT_MAX_AGENTS = 1
DEFAULT_BASE_BRANCH = "main"
# The env var NAMING this runner's hub bearer token — the toml round-trips the
# variable name only, never the secret.
DEFAULT_TOKEN_ENV = "BZ_HUB_TOKEN"
# The env var NAMING the secret that signs this runner's session cookie, likewise name-only in the toml.
DEFAULT_SESSION_SECRET_ENV = "BZ_RUNNER_SESSION_SECRET"
# HMAC-SHA256's block-safe floor: a decoded secret shorter than this is refused at load.
MIN_SESSION_SECRET_BYTES = 32
DEFAULT_ENV_POOL: tuple[str, ...] = ("e1",)
DEFAULT_MAX_ENVIRONMENTS = 10
# The runner-ceiling rolling window's default length — a ceiling with no
# declared window still needs one to sum over.
DEFAULT_RUNNER_CEILING_WINDOW_HOURS = 24.0
# How often the tick re-samples the harness's rate-limit windows — a
# diagnostic, best-effort read, not a spend control.
DEFAULT_EXTERNAL_USAGE_SAMPLE_INTERVAL_SECONDS = 300
# Structural-only: `[[subscription]]` required keys — `provider` is
# unvalidated, since an undeclared sampler binding is simply unsampled, not a load failure.
_REQUIRED_SUBSCRIPTION_KEYS = ("slug", "name", "provider")
# Well under the minutes a context takes to move; each read is a bounded tail read.
DEFAULT_CONTEXT_SAMPLE_INTERVAL_SECONDS = 60
DEFAULT_AUTH_HUB_ROLE = "mirror"
# How long a worker's captured stdout/stderr survive after release before the periodic
# sweep prunes them — long enough to investigate a stalled or rate-limited
# invocation days after the fact.
DEFAULT_WORKER_STDOUT_RETENTION_DAYS = 14


def resolve_session_secret(env_name: str) -> bytes:
    """The session-signing secret held (base64) in the env var *env_name*; ``b""`` when unset or
    empty. A value that is not base64 or decodes under :data:`MIN_SESSION_SECRET_BYTES` raises a
    :class:`ConfigError` naming the variable and never the value."""
    raw = os.environ.get(env_name, "").strip()
    if not raw:
        return b""
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise ConfigError(f"{env_name} must be base64 (e.g. `openssl rand -base64 48`)") from None
    if len(decoded) < MIN_SESSION_SECRET_BYTES:
        raise ConfigError(f"{env_name} must decode to at least {MIN_SESSION_SECRET_BYTES} bytes")
    return decoded


def effective_autonomy_source(sections: HarnessSections, shared_key_set: bool) -> str:
    """Where the effective autonomy posture comes from: the first binding's legacy override key,
    else `[harness] autonomy` when the key is set, else `"default"`."""
    overrides = [override for s in sections if (override := s.autonomy_override()) is not None]
    if overrides:
        return overrides[0]
    return "[harness] autonomy" if shared_key_set else "default"


def _parse_autonomy(value: object, path: Path) -> Autonomy:
    """The ``[harness] autonomy`` value; absent resolves to :attr:`Autonomy.Dangerous`."""
    if value is None:
        return Autonomy.Dangerous
    try:
        return Autonomy(value)
    except ValueError:
        allowed = ", ".join(repr(a.value) for a in Autonomy)
        raise ConfigError(f"[harness] autonomy must be one of {allowed}, got {value!r} (in {path})") from None


def _parse_harness_config_dir(value: object, root: Path, path: Path) -> Path | None:
    """The ``[harness] config_dir`` operator bundle: ``~`` expands, the path must be absolute,
    and it may not be the runner's own ``harness-config`` root or inside it."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"[harness] config_dir must be a non-empty path string, got {value!r} (in {path})")
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        raise ConfigError(f"[harness] config_dir must be absolute (~ allowed), got {value!r} (in {path})")
    effective = (root / HARNESS_CONFIG_DIRNAME).resolve()
    if expanded.resolve().is_relative_to(effective):
        raise ConfigError(
            f"[harness] config_dir {value!r} is the runner's own {effective} or inside it; "
            f"name a directory the operator owns (in {path})"
        )
    return expanded


def _workspace_repos(raw: object) -> tuple[WorkspaceRepo, ...]:
    if not isinstance(raw, list):
        raise ConfigError("[[workspace_repo]] must be an array of tables")
    repos: list[WorkspaceRepo] = []
    for entry in raw:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not isinstance(entry.get("url"), str)
        ):
            raise ConfigError("[[workspace_repo]] requires name and url strings")
        name, url = entry["name"], entry["url"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name) or not url.strip():
            raise ConfigError(f"invalid [[workspace_repo]] name or url: {entry!r}")
        if any(repo.name == name for repo in repos):
            raise ConfigError(f"duplicate [[workspace_repo]] name {name!r}")
        repos.append(WorkspaceRepo(name, url))
    return tuple(repos)


def _expanded_path_prepend(raw: tuple[str, ...]) -> tuple[str, ...]:
    """Expand ``~`` in each ``[worker] path_prepend`` entry and store it absolute; a
    still-relative entry after expansion raises, naming the offending key."""
    expanded: list[str] = []
    for entry in raw:
        path = Path(entry).expanduser()
        if not path.is_absolute():
            raise ConfigError(f"[worker] path_prepend entries must be absolute (~ allowed), got {entry!r}")
        expanded.append(str(path))
    return tuple(expanded)


def _cap_line(key: str, value: int | None, default: int) -> str:
    """One ``[transcripts]`` ceiling: live once overridden, commented at its default so the
    scaffolded file always shows an operator what the ceiling IS."""
    return f"{key} = {value}\n" if value is not None else f"# {key} = {default}\n"


@domain_model
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
    def window_hours(self) -> float:  # ast-grep-ignore: bzh:property-delegates
        """Defaulted whether or not a ceiling is set alongside it."""
        hours = self.table.real("window_hours")
        return DEFAULT_RUNNER_CEILING_WINDOW_HOURS if hours is None else hours


@domain_model
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
    def warn_tokens(self) -> int | None:  # ast-grep-ignore: bzh:property-delegates
        """The line a running session's context is warned about crossing; absent = no lane,
        and nothing is sampled at all."""
        return self.table.count("warn_tokens", 0) or None

    @property
    def sample_interval_seconds(self) -> int:
        return self.table.count("sample_interval_seconds", DEFAULT_CONTEXT_SAMPLE_INTERVAL_SECONDS)


@domain_model
@dataclass(frozen=True)
class ExternalUsage:
    """The ``[external_subscription_usage]`` table."""

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


@domain_model
@dataclass(frozen=True)
class SubscriptionDeclaration:
    """One declared provider subscription — the runner-unique, immutable
    join key everything downstream keys on is ``slug``; ``name`` is operator-facing only.

    Declarations win over the legacy ``[external_subscription_usage]`` table
    deterministically: when any ``[[subscription]]`` is present, the legacy table is not
    consulted; when none is, :meth:`synthesized_from_legacy` produces the sole runtime
    entry, under :data:`LEGACY_ANTHROPIC_SLUG`."""

    slug: str
    name: str
    provider: str
    #: Reuses the legacy field's shape — ``None`` means the sampler
    #: binding's own default.
    credentials_path: str | None = None
    sample_interval_seconds: int = DEFAULT_EXTERNAL_USAGE_SAMPLE_INTERVAL_SECONDS

    @classmethod
    def declared(cls, raw_declarations: object) -> tuple[SubscriptionDeclaration, ...]:
        """Validate and project ``[[subscription]]`` entries; each rejection names the
        offending entry. An empty list (none authored) is zero declarations, filled in by
        :meth:`synthesized_from_legacy`; anything else non-list (e.g. a singular
        ``[subscription]`` table) is rejected rather than silently read as zero."""
        if not isinstance(raw_declarations, list):
            raise ConfigError(
                f"[[subscription]] must be an array of tables, got {type(raw_declarations).__name__}: "
                f"{raw_declarations!r}"
            )
        declarations: list[SubscriptionDeclaration] = []
        seen_slugs: set[str] = set()
        for entry in raw_declarations:
            if not isinstance(entry, dict):
                raise ConfigError(f"[[subscription]] entry must be a table, got {entry!r}")
            missing = [key for key in _REQUIRED_SUBSCRIPTION_KEYS if key not in entry]
            if missing:
                raise ConfigError(f"[[subscription]] entry is missing required key(s) {missing}: {entry!r}")
            slug = str(entry["slug"])
            if not slug:
                raise ConfigError(f"[[subscription]] slug must not be empty: {entry!r}")
            if slug in seen_slugs:
                raise ConfigError(f"duplicate [[subscription]] slug {slug!r}")
            seen_slugs.add(slug)
            credentials_path = str(entry["credentials_path"]) if entry.get("credentials_path") else None
            declarations.append(
                cls(
                    slug=slug,
                    name=str(entry["name"]),
                    provider=str(entry["provider"]),
                    credentials_path=credentials_path,
                    sample_interval_seconds=int(
                        entry.get("sample_interval_seconds", DEFAULT_EXTERNAL_USAGE_SAMPLE_INTERVAL_SECONDS)
                    ),
                )
            )
        return tuple(declarations)

    @classmethod
    def synthesized_from_legacy(
        cls, *, credentials_path: str | None, sample_interval_seconds: int
    ) -> SubscriptionDeclaration:
        """The one declaration a runner with no ``[[subscription]]`` entries runs with —
        carrying the legacy ``[external_subscription_usage]`` table's own cadence and
        credential path forward unchanged."""
        return cls(
            slug=LEGACY_ANTHROPIC_SLUG,
            name=LEGACY_ANTHROPIC_NAME,
            provider=PROVIDER_ANTHROPIC,
            credentials_path=credentials_path,
            sample_interval_seconds=sample_interval_seconds,
        )


@domain_model
@dataclass(frozen=True)
class Queue:
    """The ``[queue]`` table — this runner's selection policy over the peeked
    ready queue, applied at :class:`~blizzard.runner.lifecycle.claim.ReadyQueue`'s peek seam."""

    table: Table

    @classmethod
    def of(cls, raw: object) -> Queue:
        return cls(Table.of(raw))

    @property
    def strict(self) -> bool:
        """Off by default: a marked head is reached past for the first unmarked entry.
        ``True`` holds at a marked head instead, yielding no entry rather than falling
        through — that runner's operator has chosen to idle over reaching ahead."""
        return self.table.boolean("strict", False)


@domain_model
@dataclass(frozen=True)
class WorkerStdout:
    """The ``[worker_stdout]`` table — the periodic sweep's own retention window
    over ``worker-stdout/``, independent of lease release."""

    table: Table

    @classmethod
    def of(cls, raw: object) -> WorkerStdout:
        return cls(Table.of(raw))

    @property
    def retention_days(self) -> int:
        return self.table.count("retention_days", DEFAULT_WORKER_STDOUT_RETENTION_DAYS)


@domain_model
@dataclass(frozen=True)
class Transcripts:
    """The ``[transcripts]`` table — the dedicated outbound lane's own switch,
    distinct from the top-level ``transcripts_root`` (the harness source's read location)."""

    table: Table

    @classmethod
    def of(cls, raw: object) -> Transcripts:
        return cls(Table.of(raw))

    @property
    def ship(self) -> bool:
        """Off by default — a rollout decision, not a discard-sink one: the hub's
        durable, compressed-at-rest, operator-gated segment store (``#247``) is ready to
        receive shipped segments, so a `True` value here is retained, not wasted
        bandwidth."""
        return self.table.boolean("ship", False)

    @property
    def record_max_bytes(self) -> int | None:
        """Override for the pump's own per-record cap; ``None`` keeps its default.
        Must stay at or below the hub's `record_max_bytes` — the ordering
        and its consequence are at :mod:`blizzard.runner.transcripts.caps`."""
        return self._cap("record_max_bytes")

    @property
    def chunk_max_bytes(self) -> int | None:
        """Override for the pump's own per-chunk budget; ``None`` keeps its default."""
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


@domain_model
@dataclass(frozen=True)
class Auth:
    """The ``[auth]`` table — runner-local role resolution, keyed by hub username."""

    table: Table

    @classmethod
    def of(cls, raw: object) -> Auth:
        return cls(Table.of(raw))

    @property
    def superuser(self) -> str | None:
        return self.table.word("superuser")

    @property
    def hub_role_default(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        return self.table.word("hub_role_default") or DEFAULT_AUTH_HUB_ROLE

    @property
    def users(self) -> tuple[tuple[str, str], ...]:
        return self.table.pairs("users")


@domain_model
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
    #: Names the env var carrying the hub bearer token; :attr:`hub_token` is
    #: the resolved secret, and empty is a valid state.
    token_env: str = DEFAULT_TOKEN_ENV
    hub_token: str = ""
    #: Names the env var carrying the session-signing secret; :attr:`session_secret` is the
    #: resolved bytes, empty meaning a fresh random secret per process.
    session_secret_env: str = DEFAULT_SESSION_SECRET_ENV
    session_secret: bytes = field(default=b"", repr=False)
    workspace_root: str = ""  # the winter workspace the provider drives; required to FILL
    workspace_provider: str = "winter"
    workspace_repos: tuple[WorkspaceRepo, ...] = ()
    max_environments: int = DEFAULT_MAX_ENVIRONMENTS
    workspace_envs: tuple[str, ...] = DEFAULT_ENV_POOL  # the provider's static env pool
    #: `[harness] autonomy`, the runner-wide approval posture each harness binding translates.
    autonomy: Autonomy = Autonomy.Dangerous
    #: Where :attr:`autonomy`'s posture comes from: a binding's legacy override, `[harness] autonomy`, or `"default"`.
    autonomy_source: str = "default"
    #: `[harness] config_dir`, the operator-owned harness-config bundle; `None` is no bundle.
    harness_config_dir: Path | None = None
    #: Every harness binding's own config section, in catalog order.
    harness_sections: HarnessSections = field(default_factory=HarnessSections.defaults)
    max_agents: int = DEFAULT_MAX_AGENTS
    base_branch: str = DEFAULT_BASE_BRANCH
    #: Node NAMES this runner imposes a human gate on; reloaded every tick.
    gates: tuple[str, ...] = ()
    #: The workspace prompt prepended to a worker spawn — two source knobs,
    #: one effective value (:meth:`resolved_workspace_prompt`); the file wins when set.
    workspace_prompt: str = ""
    workspace_prompt_file: str = ""
    #: A packaged workspace-prompt sample, by name; exclusive with the two above.
    workspace_prompt_package: str = ""
    #: The override of the baked-in blizzard preamble, prepended ahead of
    #: :attr:`workspace_prompt`; empty resolves to the baked default.
    runner_prompt: str = ""
    runner_prompt_file: str = ""
    #: Where the harness writes session transcripts; read from the toml, never
    #: re-read from the environment live, so a changed env var needs a re-``init``.
    transcripts_root: str = ""
    #: Per-record cap and per-chunk budget overrides (``[transcripts]``);
    #: ``None`` keeps `transcript_pump`'s own defaults, which own the values.
    transcript_record_max_bytes: int | None = None
    transcript_chunk_max_bytes: int | None = None
    #: The transcript outbound lane's own switch (``[transcripts] ship``);
    #: off by default — the pump enqueues no delta while this is ``False``.
    transcripts_ship: bool = False
    #: This runner's selection policy over the peeked ready queue (``[queue] strict``);
    #: off by default reaches past a marked head for the first unmarked
    #: entry, ``True`` holds at a marked head and yields no entry instead.
    queue_strict: bool = False
    #: The per-chunk spend cap; ``None`` means no cap. A chunk reaching it
    #: parks ``needs_human`` at its next step boundary.
    chunk_cap_usd: float | None = None
    #: The runner-wide spend ceiling; ``None`` means none. Crossing it engages
    #: the local pause brake, and there is no auto-unpause once the window drops back under.
    runner_ceiling_usd: float | None = None
    #: The runner ceiling's rolling window length in hours — unused while
    #: :attr:`runner_ceiling_usd` is ``None``.
    runner_ceiling_window_hours: float = DEFAULT_RUNNER_CEILING_WINDOW_HOURS
    #: The external-usage sample cadence in seconds — a diagnostic cadence,
    #: not a spend control, so absent means the default rather than never.
    external_usage_sample_interval_seconds: int = DEFAULT_EXTERNAL_USAGE_SAMPLE_INTERVAL_SECONDS
    #: An override for the credential file the external-usage sampler reads;
    #: ``None`` means the adapter's own default.
    external_usage_credentials_path: str | None = None
    #: Every authored ``[[subscription]]`` entry, verbatim — empty when none
    #: is declared. :meth:`resolved_subscriptions` is the runtime list every caller other
    #: than the config layer itself should read: this field alone says nothing about the
    #: legacy ``[external_subscription_usage]`` table's own implicit subscription.
    subscriptions: tuple[SubscriptionDeclaration, ...] = ()
    #: The session-context warn line; ``None`` disables the lane — nothing is sampled, nothing gated.
    context_warn_tokens: int | None = None
    #: How often a running lease's context is re-read — unused while the lane is off.
    context_sample_interval_seconds: int = DEFAULT_CONTEXT_SAMPLE_INTERVAL_SECONDS
    #: The declared extension to the worker spawn-environment allowlist — a
    #: worker's env is that allowlist, never a full ``os.environ`` copy.
    worker_env_passthrough: tuple[str, ...] = ()
    #: Absolute directories (``~`` expanded at load) led onto every worker's ``PATH`` ahead of the daemon's own.
    worker_path_prepend: tuple[str, ...] = ()
    #: Every browser-reachable origin this runner answers on, authored as `public_url` — one URL or
    #: a list; first is canonical, empty registers no federation identity.
    public_urls: tuple[str, ...] = ()
    #: The hub username naming this runner's own sovereign — config-only,
    #: never assignable through a JWT claim.
    auth_superuser: str | None = None
    #: The fallback role for a hub identity with no `[auth.users]` override —
    #: `"mirror"` reproduces the hub's claim, a fixed role floors every unmatched identity.
    auth_hub_role_default: str = DEFAULT_AUTH_HUB_ROLE
    #: Per-username role overrides, keyed on the JWT's `username` claim only,
    #: never `email`, which is mutable and may be null.
    auth_users: tuple[tuple[str, str], ...] = ()
    #: The reverse-proxy trust set — addresses or CIDRs whose
    #: `X-Forwarded-Proto` is honored; empty ignores the header from every peer.
    trusted_proxies: tuple[str, ...] = ()
    #: How long (days) a worker's captured stdout/stderr survive after being written, before
    #: the periodic sweep prunes them (``[worker_stdout] retention_days``) —
    #: independent of lease release, which leaves them in place.
    worker_stdout_retention_days: int = DEFAULT_WORKER_STDOUT_RETENTION_DAYS
    #: Lease-trace sweep knobs; every field at its default when ``[tracing]`` is absent.
    tracing: TracingConfig = field(default_factory=TracingConfig)

    @property
    def worker_env(self) -> AllowlistedEnv:
        """The one allowlisted env every runner-spawned child is built from
        (``bzh:worker-env-allowlist``) — the sole accessor a composition root reads instead
        of constructing an :class:`AllowlistedEnv` from the raw fields itself."""
        return self._build_worker_env()

    def _build_worker_env(self) -> AllowlistedEnv:
        # `WINTER_OTEL_*` is withheld unconditionally: the runner alone hands winter its exporter, and only under
        # `worker_programs`, so a passed-through value would skip the receiver's lease check and redaction.
        dropped = {
            *self.dropped_otel_passthrough,
            *(n for n in self.worker_env_passthrough if n.startswith(_WINTER_OTEL_PREFIX)),
        }
        passthrough = tuple(name for name in self.worker_env_passthrough if name not in dropped)
        return AllowlistedEnv.of(passthrough, path_prepend=self.worker_path_prepend)

    @property
    def dropped_otel_passthrough(self) -> tuple[str, ...]:  # ast-grep-ignore: bzh:property-delegates
        """The ``OTEL_*`` and ``WINTER_OTEL_*`` names in ``[worker] env_passthrough`` that are withheld from every
        worker while tracing is on — a worker must never inherit the runner's exporter configuration or
        credentials. ``host``'s own startup warning names them."""
        if not TracingSettings.of(os.environ).enabled():
            return ()
        return tuple(name for name in self.worker_env_passthrough if name.startswith(("OTEL_", _WINTER_OTEL_PREFIX)))

    @property
    def missing_worker_path_prepend_entries(self) -> tuple[str, ...]:  # ast-grep-ignore: bzh:property-delegates
        """Every configured ``[worker] path_prepend`` entry absent on disk right now —
        ``host``'s own startup warning reads this; a missing entry still starts the runner,
        it just never contributes to a spawned child's ``PATH``."""
        return tuple(entry for entry in self.worker_path_prepend if not Path(entry).exists())

    @property
    def public_origins(self) -> PublicOrigins:
        """Every origin this runner answers on, in declaration order."""
        return PublicOrigins.of(*self.public_urls)

    @property
    def public_url(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """The canonical origin — the first declared. It is what the hub records as this runner's own
        URL, and what a request whose ``Host`` matches no declared origin falls back to. Empty when
        none is declared, which is how a runner registers no federation identity at all."""
        return self.public_origins.canonical or ""

    @property
    def role_policy(self) -> RolePolicy:
        """The ``[auth]`` role precedence, as the federation callback resolves a local role by it."""
        return RolePolicy(
            superuser=self.auth_superuser, users=self.auth_users, hub_role_default=self.auth_hub_role_default
        )

    @property
    def redirect_uris(self) -> tuple[str, ...]:
        """The redirect URIs this runner presents to the hub's IdP authorize endpoint — one
        per declared origin, derived from :attr:`public_origins`, never independently
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
    def effective_workspace_root(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """An absolute root shared by the hosted app and loop, independent of their cwd."""
        path = Path(self.workspace_root) if self.workspace_root else self.root / "workspace"
        return str((path if path.is_absolute() else self.root / path).resolve())

    @property
    def harness_settings(self) -> HarnessSettings:
        """The runner-wide values every harness binding is built from."""
        return HarnessSettings(
            root=self.root,
            autonomy=self.autonomy,
            config_dir=self.harness_config_dir,
            worker_env=self.worker_env,
            transcripts_root=self.transcripts_root,
            sections=self.harness_sections,
        )

    @property
    def workspace_settings(self) -> WorkspaceSettings:
        """The workspace binding's inputs, as the provider factory builds a provider from them."""
        return WorkspaceSettings(
            provider=self.workspace_provider,
            root=self.root,
            workspace_root=self.workspace_root,
            effective_workspace_root=self.effective_workspace_root,
            repos=self.workspace_repos,
            max_environments=self.max_environments,
            base_branch=self.base_branch,
            env_pool=self.workspace_envs,
        )

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
        """The effective static workspace prompt, resolved from its three knobs.

        ``workspace_prompt_package`` names a packaged sample and is exclusive with the other two,
        which keep their file-wins-over-inline precedence; a configured-but-missing sample or file
        raises, while all three empty is valid and returns ``""``."""
        if self.workspace_prompt_package:
            self._sole_workspace_prompt_knob()
            try:
                return PACKAGED.text(self.workspace_prompt_package)
            except UnknownWorkspacePromptSample as exc:
                packaged = ", ".join(PACKAGED.names) or "none"
                raise ConfigError(
                    f"workspace_prompt_package names no packaged sample: "
                    f"{self.workspace_prompt_package} (packaged: {packaged})"
                ) from exc
        if self.workspace_prompt_file:
            path = Path(self.workspace_prompt_file)
            if not path.is_absolute():
                path = self.root / path
            if not path.exists():
                raise ConfigError(f"workspace_prompt_file does not exist: {path}")
            return path.read_text()
        return self.workspace_prompt

    def _sole_workspace_prompt_knob(self) -> None:
        """Reject a named sample set alongside either text knob — the pair has no precedence rule."""
        conflicts = [
            name
            for name, value in (
                ("workspace_prompt_file", self.workspace_prompt_file),
                ("workspace_prompt", self.workspace_prompt),
            )
            if value
        ]
        if conflicts:
            raise ConfigError(f"workspace_prompt_package is exclusive with {', '.join(conflicts)} — set one of them")

    def resolved_runner_prompt(self) -> str:
        """The effective override for the blizzard preamble, from its two knobs.

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

    def resolved_subscriptions(self) -> tuple[SubscriptionDeclaration, ...]:
        """Every declared provider subscription, resolved — the runtime list
        every caller but this config layer itself should read.

        Mirrors :meth:`resolved_workspace_prompt`'s two-knobs-one-value shape: declarations
        win, deterministically. Any authored ``[[subscription]]`` (:attr:`subscriptions`)
        replaces the legacy ``[external_subscription_usage]`` table entirely; none present
        synthesizes the sole legacy-Anthropic declaration from this config's own
        already-resolved :attr:`external_usage_credentials_path` /
        :attr:`external_usage_sample_interval_seconds` — never both, and never empty."""
        return self.subscriptions or (
            SubscriptionDeclaration.synthesized_from_legacy(
                credentials_path=self.external_usage_credentials_path,
                sample_interval_seconds=self.external_usage_sample_interval_seconds,
            ),
        )

    def auth_headers(self) -> dict[str, str]:
        """The outbound ``Authorization`` header every runner->hub call carries.

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
            workspace_provider="basic" if not os.environ.get(ENV_WORKSPACE_ROOT) else "winter",
            workspace_envs=tuple(e.strip() for e in envs.split(",") if e.strip()) if envs else DEFAULT_ENV_POOL,
            base_branch=os.environ.get(ENV_BASE_BRANCH, DEFAULT_BASE_BRANCH),
            gates=tuple(g.strip() for g in gates.split(",") if g.strip()) if gates else (),
            harness_sections=HarnessSections.scaffold(root, os.environ),
            # Empty on a fresh scaffold; seeded from the environment so `init` can inject
            # a default without hand-editing.
            workspace_prompt=os.environ.get(ENV_WORKSPACE_PROMPT, ""),
            # Seeded the same way, so `init` can adopt a packaged sample by name.
            workspace_prompt_package=os.environ.get(ENV_WORKSPACE_PROMPT_PACKAGE, ""),
            # Empty on a fresh scaffold means the baked-in preamble is used.
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
        # `json.dumps` emits a valid TOML basic string: TOML shares JSON's escapes
        # (\n, \t, \", \\, \uXXXX), so a multi-line inline prompt round-trips intact.
        workspace_prompt = json.dumps(self.workspace_prompt)
        workspace_prompt_file = json.dumps(self.workspace_prompt_file)
        workspace_prompt_package = json.dumps(self.workspace_prompt_package)
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
            "\n# Reverse-proxy trust set: proxy IPs/CIDRs whose X-Forwarded-Proto is\n"
            "# honored when minting the SSO session cookie's Secure flag. Empty = header ignored.\n"
            "# Required for any https origin above, and the proxy must also pass the browser's\n"
            "# original Host through — selection reads it, and nginx replaces it by default.\n"
            f"trusted_proxies = [{', '.join(f'"{p}"' for p in self.trusted_proxies)}]\n"
            "\n# Names the env var carrying this runner's hub bearer token;\n"
            "# the secret itself lives in the runtime env file, never here.\n"
            f'token_env = "{self.token_env}"\n'
            "\n# Names the env var carrying the secret that signs this runner's session cookie\n"
            "# (base64, >= 32 bytes decoded; unique per runner). Unset = a fresh secret each start.\n"
            f'session_secret_env = "{self.session_secret_env}"\n'
            f'runner_id = "{self.runner_id}"\n'
            f'workspace_id = "{self.workspace_id}"\n'
            f'workspace_root = "{self.workspace_root}"\n'
            f'workspace_provider = "{self.workspace_provider}"\n'
            f"max_environments = {self.max_environments}\n"
            f"workspace_envs = [{envs}]\n"
            "# Basic: add a [[workspace_repo]] for each git origin (name and url).\n"
            "# Released folders remain for inspection until the cap needs room; oldest\n"
            "# unheld folders are evicted first. Reacquisition resets all repo worktrees.\n"
            "# A commented [[workspace_repo]] example is at the end of this file.\n"
            + "".join(section.root_toml() for section in self.harness_sections)
            + f"max_agents = {self.max_agents}\n"
            f'base_branch = "{self.base_branch}"\n'
            "\n# Human gates this runner imposes by node name; empty = none.\n"
            f"gates = [{gates}]\n"
            "\n# The runner-owned workspace prompt prepended to a worker spawn.\n"
            "# `workspace_prompt` is inline text; `workspace_prompt_file` (a path) wins when set.\n"
            "# `workspace_prompt_package` names a sample shipped in the wheel and may not be\n"
            "# combined with either (`blizzard runner prompt list` names them).\n"
            "# Empty = table-only injection. Replace at runtime via PUT /api/workspace-prompt.\n"
            "# A resumed spawn re-sends this only when it changed, announced as updated.\n"
            f"workspace_prompt = {workspace_prompt}\n"
            f"workspace_prompt_file = {workspace_prompt_file}\n"
            f"workspace_prompt_package = {workspace_prompt_package}\n"
            "\n# The operator's override of the baked-in blizzard preamble — layer 1\n"
            "# of the spawn preamble, ahead of `workspace_prompt` above. `runner_prompt` is inline\n"
            "# text; `runner_prompt_file` (a path) wins when set. Empty = the baked default\n"
            "# (DEFAULT_BLIZZARD_PREAMBLE) is used instead; config/startup only, no runtime override.\n"
            f"runner_prompt = {runner_prompt}\n"
            f"runner_prompt_file = {runner_prompt_file}\n"
            "\n# Where the coding harness writes session transcripts;\n"
            "# empty = ~/.claude/projects.\n"
            f'transcripts_root = "{self.transcripts_root}"\n'
            "\n# How freely an unattended worker may act without a human approving tool use:\n"
            '# "normal", "auto", or "dangerous". Each harness translates it into its own terms.\n'
            "[harness]\n"
            f'autonomy = "{self.autonomy}"\n'
            "# An operator-owned bundle of harness configuration: optional `claude-code/` and\n"
            "# `opencode/` directories, read at startup. Absolute path (~ allowed); absent = no bundle.\n"
            + (
                f"config_dir = {json.dumps(str(self.harness_config_dir))}\n"
                if self.harness_config_dir
                else '# config_dir = "~/.config/blizzard/harness"\n'
            )
            + "\n# The transcript outbound lane — off by default; the hub's own\n"
            "# durable, compressed-at-rest segment store is already landed, so\n"
            "# turning this on is a rollout decision, not a bandwidth-for-nothing one.\n"
            "[transcripts]\n"
            f"ship = {'true' if self.transcripts_ship else 'false'}\n"
            "# This lane's own byte ceilings, shown at their defaults;\n"
            "# uncomment to override. Widen `chunk_max_bytes` for a backfill window — a\n"
            "# `blizzard runner transcript reship` spends that budget a SECOND time over the\n"
            "# same chunk — then restore it. Keep `record_max_bytes` at or BELOW the hub's own\n"
            "# `record_max_bytes`: over the hub's, a record loses its turns whole; over this\n"
            "# one, the pump merely shrinks them.\n"
            + _cap_line("record_max_bytes", self.transcript_record_max_bytes, TRANSCRIPT_RECORD_MAX_BYTES)
            + _cap_line("chunk_max_bytes", self.transcript_chunk_max_bytes, CHUNK_TRANSCRIPT_MAX_BYTES)
            + "\n# This runner's selection over the peeked ready queue; off by\n"
            "# default reaches past a marked head for the first unmarked entry. `true` holds\n"
            "# at a marked head instead and idles rather than falling through.\n"
            "[queue]\n"
            f"strict = {'true' if self.queue_strict else 'false'}\n"
            + "\n# How long (days) a worker's captured stdout/stderr survive after being\n"
            "# written, before the periodic sweep prunes them. A released lease's files are NOT\n"
            "# deleted at release — only this age-based sweep removes them, both streams alike.\n"
            "# Look up one invocation's own output at\n"
            "# worker-stdout/<lease_id>.<generation>.{stdout,stderr}.\n"
            "[worker_stdout]\n"
            f"retention_days = {self.worker_stdout_retention_days}\n"
            + "".join(self.tracing.to_toml(unit="closed leases", receiver=True))
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
            + "# windows — a diagnostic, best-effort read, not a spend control.\n"
            + "[external_subscription_usage]\n"
            + f"sample_interval_seconds = {self.external_usage_sample_interval_seconds}\n"
            + (
                f'credentials_path = "{self.external_usage_credentials_path}"\n'
                if self.external_usage_credentials_path is not None
                else '# credentials_path = "/path/to/.credentials.json"  # defaults to ~/.claude/.credentials.json\n'
            )
            + "\n# Declared provider subscriptions — the join key everything\n"
            + "# downstream keys on is `slug`, runner-unique and immutable once observed. Absent\n"
            + "# entirely (the default): the `[external_subscription_usage]` table above is the\n"
            + '# sole subscription, synthesized under the reserved slug "'
            + LEGACY_ANTHROPIC_SLUG
            + '". Any\n'
            + "# `[[subscription]]` present here instead takes over completely — the legacy table\n"
            + "# is no longer consulted for the runtime list.\n"
            + "".join(
                "\n[[subscription]]\n"
                f'slug = "{d.slug}"\n'
                f'name = "{d.name}"\n'
                f'provider = "{d.provider}"\n'
                f"sample_interval_seconds = {d.sample_interval_seconds}\n"
                + (f'credentials_path = "{d.credentials_path}"\n' if d.credentials_path is not None else "")
                for d in self.subscriptions
            )
            + "\n# The worker spawn-environment allowlist's operator extension (`bzh:worker-env-allowlist`).\n"
            + "# The base allowlist (PATH/HOME/USER/LANG/LC_*/TERM/TMPDIR) always reaches a worker;\n"
            + "# name additional vars here to forward them too. Empty = base allowlist only. The\n"
            + "# BLIZZARD_* identity vars are injected per spawn/judge/resume, not passed through.\n"
            + "[worker]\n"
            + f"env_passthrough = [{', '.join(f'"{v}"' for v in self.worker_env_passthrough)}]\n"
            + "# Absolute directories (~ allowed) led onto every worker's PATH, ahead of the\n"
            + "# daemon's own — e.g. a mise shims dir, so a spawned worker resolves the same\n"
            + "# version-manager tools an operator's shell does. A relative entry fails config\n"
            + "# load. `runner host` warns at startup for any entry missing on disk, but still\n"
            + "# starts. Empty = the daemon's own PATH, unchanged.\n"
            + f"path_prepend = [{', '.join(f'"{p}"' for p in self.worker_path_prepend)}]\n"
            + "\n# Runner-local role resolution, keyed by hub username — lives only here,\n"
            + '# never in the hub store/admin page. `hub_role_default` is "mirror" or a fixed cap\n'
            + '# ("contributor"/"guest"/"pending"); `superuser` names this runner\'s own sovereign.\n'
            + "[auth]\n"
            + (f'superuser = "{self.auth_superuser}"\n' if self.auth_superuser else '# superuser = "<hub-username>"\n')
            + f'hub_role_default = "{self.auth_hub_role_default}"\n'
            + "\n[auth.users]\n"
            + "".join(f'{username} = "{role}"\n' for username, role in self.auth_users)
            + "".join(section.table_toml() for section in self.harness_sections)
            + "".join(
                f"\n[[workspace_repo]]\nname = {json.dumps(repo.name)}\nurl = {json.dumps(repo.url)}\n"
                for repo in self.workspace_repos
            )
            + (
                '\n# [[workspace_repo]]\n# name = "my-repo"\n# url = "https://github.com/you/my-repo.git"\n'
                if not self.workspace_repos
                else ""
            )
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
        session_secret_env = str(raw.get("session_secret_env", DEFAULT_SESSION_SECRET_ENV))
        spend = Spend.of(raw.get("cost"))
        usage = ExternalUsage.of(raw.get("external_subscription_usage"))
        context = Context.of(raw.get("context"))
        auth = Auth.of(raw.get("auth"))
        transcripts = Transcripts.of(raw.get("transcripts"))
        queue = Queue.of(raw.get("queue"))
        worker_stdout = WorkerStdout.of(raw.get("worker_stdout"))
        # Authored `[[subscription]]` entries, verbatim — never synthesized here;
        # `resolved_subscriptions()` is where declarations-win-over-the-legacy-table
        # actually happens, from this config's own resolved fields.
        subscriptions = SubscriptionDeclaration.declared(raw.get("subscription", []))
        harness_sections = HarnessSections.parse(raw, root=root, path=path)
        harness = Table.of(raw.get("harness"))
        autonomy = _parse_autonomy(harness.body.get("autonomy"), path)
        autonomy_source = effective_autonomy_source(harness_sections, "autonomy" in harness.body)
        harness_config_dir = _parse_harness_config_dir(harness.body.get("config_dir"), root, path)
        provider = raw.get("workspace_provider", "winter")
        if provider not in WORKSPACE_PROVIDERS:
            names = " or ".join(repr(name) for name in WORKSPACE_PROVIDERS)
            raise ConfigError(f"workspace_provider must be {names}, got {provider!r}")
        cap = raw.get("max_environments", DEFAULT_MAX_ENVIRONMENTS)
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
            raise ConfigError(f"max_environments must be a positive integer, got {cap!r}")
        repos = _workspace_repos(raw.get("workspace_repo", []))
        return cls(
            root=root,
            db_url=str(raw["db_url"]),
            host=host or str(raw.get("host", DEFAULT_HOST)),
            port=port if port is not None else int(raw.get("port", DEFAULT_PORT)),
            hub_url=str(raw.get("hub_url", DEFAULT_HUB_URL)),
            token_env=token_env,
            hub_token=os.environ.get(token_env, ""),
            session_secret_env=session_secret_env,
            session_secret=resolve_session_secret(session_secret_env),
            runner_id=str(raw.get("runner_id", DEFAULT_RUNNER_ID)),
            workspace_id=str(raw.get("workspace_id", DEFAULT_WORKSPACE_ID)),
            workspace_root=str(raw.get("workspace_root", "")),
            workspace_provider=provider,
            workspace_repos=repos,
            max_environments=cap,
            workspace_envs=Table.of(raw).listed("workspace_envs", DEFAULT_ENV_POOL),
            harness_sections=harness_sections,
            autonomy=autonomy,
            autonomy_source=autonomy_source,
            harness_config_dir=harness_config_dir,
            max_agents=int(raw.get("max_agents", DEFAULT_MAX_AGENTS)),
            base_branch=str(raw.get("base_branch", DEFAULT_BASE_BRANCH)),
            gates=tuple(str(g) for g in raw.get("gates", ())),
            workspace_prompt=str(raw.get("workspace_prompt", "")),
            workspace_prompt_file=str(raw.get("workspace_prompt_file", "")),
            workspace_prompt_package=str(raw.get("workspace_prompt_package", "")),
            runner_prompt=str(raw.get("runner_prompt", "")),
            runner_prompt_file=str(raw.get("runner_prompt_file", "")),
            transcripts_root=str(raw.get("transcripts_root", "")),
            transcripts_ship=transcripts.ship,
            transcript_record_max_bytes=transcripts.record_max_bytes,
            transcript_chunk_max_bytes=transcripts.chunk_max_bytes,
            queue_strict=queue.strict,
            chunk_cap_usd=spend.chunk_cap_usd,
            runner_ceiling_usd=spend.ceiling_usd,
            runner_ceiling_window_hours=spend.window_hours,
            external_usage_sample_interval_seconds=usage.sample_interval_seconds,
            external_usage_credentials_path=usage.credentials_path,
            subscriptions=subscriptions,
            context_warn_tokens=context.warn_tokens,
            context_sample_interval_seconds=context.sample_interval_seconds,
            worker_env_passthrough=Table.of(raw.get("worker")).names("env_passthrough"),
            worker_path_prepend=_expanded_path_prepend(Table.of(raw.get("worker")).names("path_prepend")),
            public_urls=PublicOrigins.entries(raw.get("public_url"), ConfigError),
            auth_superuser=auth.superuser,
            auth_hub_role_default=auth.hub_role_default,
            auth_users=auth.users,
            trusted_proxies=TrustedProxies.entries(raw.get("trusted_proxies"), ConfigError),
            worker_stdout_retention_days=worker_stdout.retention_days,
            tracing=TracingConfig.of(raw.get("tracing", {}), ConfigError),
        )
