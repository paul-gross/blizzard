"""Every key the host reads from ``blizzard-host.toml``, and every durable path under its runtime root.

The registry :mod:`blizzard.runner.host_config` reads through; ``blizzard-export-contracts`` commits it as
``contracts/config/host.json``. A harness section declares its own keys beside its parser
(:attr:`~blizzard.runner.harness.declaration.IHarnessSectionKind.keys`), and this module composes them under
``runner[]``. Retired and refused keys stay declared ``deprecated``: refusing one is still a read of it."""

from __future__ import annotations

from collections.abc import Iterable

from blizzard.foundation.config_keys import (
    ARRAY_OF_TABLES,
    BOOLEAN,
    INTEGER,
    NUMBER,
    OPEN_TABLE,
    STRING,
    STRING_LIST,
    STRING_OR_LIST,
    TABLE,
    ConfigKey,
    ConfigRegistry,
    DurablePath,
)
from blizzard.foundation.trace_export.config import TRACING_KEYS
from blizzard.runner.config import (
    DEFAULT_AUTH_HUB_ROLE,
    DEFAULT_BASE_BRANCH,
    DEFAULT_CONTEXT_SAMPLE_INTERVAL_SECONDS,
    DEFAULT_ENV_POOL,
    DEFAULT_EXTERNAL_USAGE_SAMPLE_INTERVAL_SECONDS,
    DEFAULT_HOST,
    DEFAULT_HUB_URL,
    DEFAULT_MAX_ENVIRONMENTS,
    DEFAULT_PORT,
    DEFAULT_QUEUE_STRICT,
    DEFAULT_RUNNER_CEILING_WINDOW_HOURS,
    DEFAULT_SESSION_SECRET_ENV,
    DEFAULT_TRANSCRIPTS_SHIP,
    DEFAULT_WORKER_STDOUT_RETENTION_DAYS,
)
from blizzard.runner.harness.wiring import HARNESS_SECTION_KINDS
from blizzard.runner.host_layout import (
    DATA_DIRNAME,
    HOST_CONFIG_FILENAME,
    HOST_LOCK_FILENAME,
    HOST_SOCKET_FILENAME,
    HOST_STORE_FILENAME,
    RUNNERS_DIRNAME,
)
from blizzard.runner.hub.token_file import ENV_FILENAME
from blizzard.runner.throttle.settings import DEFAULT_PACE_LEAD_PCT
from blizzard.runner.tracing.config import RUNNER_TRACING_KEYS

HOST_REGISTRY_NAME = "host"

#: The flat runner file's keys graduation retired, with their type and where each went (``None``: nowhere).
_RETIRED: dict[str, tuple[str, str | None]] = {
    "workspace_id": (STRING, "workspace[].id"),
    "workspace_root": (STRING, "workspace[].root"),
    "workspace_provider": (STRING, "workspace[].provider"),
    "workspace_envs": (STRING_LIST, "workspace[].envs"),
    "workspace_repo": (ARRAY_OF_TABLES, "workspace[].repo"),
    "workspace_prompt": (STRING, "workspace[].prompt"),
    "workspace_prompt_file": (STRING, "workspace[].prompt_file"),
    "workspace_prompt_package": (STRING, "workspace[].prompt_package"),
    "token_env": (STRING, None),
    "runner_id": (STRING, "runner[].id"),
    "max_agents": (INTEGER, "runner[].agents"),
    "external_subscription_usage": (TABLE, "subscription"),
}
#: Keys that belong to a ``[[runner]]`` and are refused at the top level.
_RUNNER_HOME = (
    ("name", STRING),
    ("projects", ARRAY_OF_TABLES),
    ("gates", STRING_LIST),
    ("runner_prompt", STRING),
    ("runner_prompt_file", STRING),
    ("transcripts_root", STRING),
    ("harness", TABLE),
    *((kind.table, TABLE) for kind in HARNESS_SECTION_KINDS),
    ("models", TABLE),
    ("effort", TABLE),
    ("cost", TABLE),
    ("queue", TABLE),
    ("context", TABLE),
    ("worker", TABLE),
    ("harness_binary", STRING),
    ("harness_permission_mode", STRING),
    ("worker_settings_path", STRING),
    ("claude_code_credentials_path", STRING),
)
RUNNER_HOME_KEYS = tuple(name for name, _ in _RUNNER_HOME)
#: Keys that belong to a ``[[workspace]]`` and are refused at the top level.
_WORKSPACE_HOME = (
    ("root", STRING),
    ("provider", STRING),
    ("envs", STRING_LIST),
    ("repo", ARRAY_OF_TABLES),
    ("prompt", STRING),
    ("prompt_file", STRING),
    ("prompt_package", STRING),
    ("max_environments", INTEGER),
    ("base_branch", STRING),
)


def _prefixed(prefix: str, keys: Iterable[ConfigKey]) -> tuple[ConfigKey, ...]:
    return tuple(
        ConfigKey(
            f"{prefix}.{key.path}",
            key.type,
            key.required,
            key.default,
            key.deprecated,
            None if key.replaced_by is None else f"{prefix}.{key.replaced_by}",
        )
        for key in keys
    )


def _deprecated(entries: Iterable[tuple[str, str]], home: str) -> tuple[ConfigKey, ...]:
    return tuple(ConfigKey(name, kind, deprecated=True, replaced_by=f"{home}[].{name}") for name, kind in entries)


_HOST_KEYS = (
    ConfigKey("hub_url", STRING, default=DEFAULT_HUB_URL),
    ConfigKey("host", STRING, default=DEFAULT_HOST),
    ConfigKey("port", INTEGER, default=DEFAULT_PORT),
    ConfigKey("db_url", STRING),
    ConfigKey("public_url", STRING_OR_LIST),
    ConfigKey("trusted_proxies", STRING_LIST),
    ConfigKey("session_secret_env", STRING, default=DEFAULT_SESSION_SECRET_ENV),
    ConfigKey("host_token", STRING),
    ConfigKey("hub_tokens", OPEN_TABLE),
    ConfigKey("flags", OPEN_TABLE),
    ConfigKey("auth", TABLE),
    ConfigKey("auth.superuser", STRING),
    ConfigKey("auth.hub_role_default", STRING, default=DEFAULT_AUTH_HUB_ROLE),
    ConfigKey("auth.users", OPEN_TABLE),
    *TRACING_KEYS,
    *RUNNER_TRACING_KEYS,
    ConfigKey("transcripts", TABLE),
    ConfigKey("transcripts.ship", BOOLEAN, default=DEFAULT_TRANSCRIPTS_SHIP),
    ConfigKey("transcripts.record_max_bytes", INTEGER),
    ConfigKey("transcripts.chunk_max_bytes", INTEGER),
    ConfigKey("worker_stdout", TABLE),
    ConfigKey("worker_stdout.retention_days", INTEGER, default=DEFAULT_WORKER_STDOUT_RETENTION_DAYS),
    ConfigKey("subscription", ARRAY_OF_TABLES),
    ConfigKey("subscription[].slug", STRING, required=True),
    ConfigKey("subscription[].name", STRING, required=True),
    ConfigKey("subscription[].provider", STRING, required=True),
    ConfigKey("subscription[].credentials_path", STRING),
    ConfigKey(
        "subscription[].sample_interval_seconds",
        INTEGER,
        default=DEFAULT_EXTERNAL_USAGE_SAMPLE_INTERVAL_SECONDS,
    ),
    ConfigKey("workspace", ARRAY_OF_TABLES),
    ConfigKey("workspace[].id", STRING, required=True),
    ConfigKey("workspace[].root", STRING, default=""),
    ConfigKey("workspace[].provider", STRING, default="winter"),
    ConfigKey("workspace[].envs", STRING_LIST, default=DEFAULT_ENV_POOL),
    ConfigKey("workspace[].max_environments", INTEGER, default=DEFAULT_MAX_ENVIRONMENTS),
    ConfigKey("workspace[].repo", ARRAY_OF_TABLES),
    ConfigKey("workspace[].repo[].name", STRING, required=True),
    ConfigKey("workspace[].repo[].url", STRING, required=True),
    ConfigKey("workspace[].base_branch", STRING, default=DEFAULT_BASE_BRANCH),
    ConfigKey("workspace[].prompt", STRING, default=""),
    ConfigKey("workspace[].prompt_file", STRING, default=""),
    ConfigKey("workspace[].prompt_package", STRING, default=""),
    ConfigKey("runner", ARRAY_OF_TABLES),
    ConfigKey("runner[].id", STRING),
    ConfigKey("runner[].name", STRING, required=True),
    ConfigKey("runner[].subscription", STRING),
    ConfigKey("runner[].projects", ARRAY_OF_TABLES, required=True),
    ConfigKey("runner[].projects[].project", STRING, required=True),
    ConfigKey("runner[].projects[].workspace", STRING, required=True),
    ConfigKey("runner[].agents", INTEGER),
    ConfigKey("runner[].projects[].agents", INTEGER, deprecated=True, replaced_by="runner[].agents"),
    ConfigKey("runner[].gates", STRING_LIST, default=()),
    ConfigKey("runner[].runner_prompt", STRING, default=""),
    ConfigKey("runner[].runner_prompt_file", STRING, default=""),
    ConfigKey("runner[].transcripts_root", STRING, default=""),
    ConfigKey("runner[].harness", TABLE),
    ConfigKey("runner[].harness.autonomy", STRING),
    ConfigKey("runner[].harness.config_dir", STRING),
    ConfigKey("runner[].worker", TABLE),
    ConfigKey("runner[].worker.env_passthrough", STRING_LIST, default=()),
    ConfigKey("runner[].worker.path_prepend", STRING_LIST, default=()),
    ConfigKey("runner[].queue", TABLE),
    ConfigKey("runner[].queue.strict", BOOLEAN, default=DEFAULT_QUEUE_STRICT),
    ConfigKey("runner[].cost", TABLE),
    ConfigKey("runner[].cost.chunk_cap_usd", NUMBER),
    ConfigKey("runner[].cost.runner_ceiling_usd", NUMBER),
    ConfigKey("runner[].cost.window_hours", NUMBER, default=DEFAULT_RUNNER_CEILING_WINDOW_HOURS),
    ConfigKey("runner[].throttle", TABLE),
    ConfigKey("runner[].throttle.pace", TABLE),
    ConfigKey("runner[].throttle.pace.window", STRING),
    ConfigKey("runner[].throttle.pace.lead_pct", NUMBER, default=DEFAULT_PACE_LEAD_PCT),
    ConfigKey("runner[].throttle.pace.taper", ARRAY_OF_TABLES),
    ConfigKey("runner[].throttle.pace.taper[].from_pct", NUMBER),
    ConfigKey("runner[].throttle.pace.taper[].lead_pct", NUMBER),
    ConfigKey("runner[].throttle.utilization", TABLE),
    ConfigKey("runner[].throttle.utilization.below_pct", NUMBER),
    ConfigKey("runner[].throttle.utilization.windows", STRING_LIST),
    ConfigKey("runner[].throttle.schedule", TABLE),
    ConfigKey("runner[].throttle.schedule.days", STRING_LIST),
    ConfigKey("runner[].throttle.schedule.hours", STRING_LIST),
    ConfigKey("runner[].context", TABLE),
    ConfigKey("runner[].context.warn_tokens", INTEGER),
    ConfigKey("runner[].context.sample_interval_seconds", INTEGER, default=DEFAULT_CONTEXT_SAMPLE_INTERVAL_SECONDS),
    *_prefixed("runner[]", (key for kind in HARNESS_SECTION_KINDS for key in kind.keys)),
    *(ConfigKey(name, kind, deprecated=True, replaced_by=home) for name, (kind, home) in _RETIRED.items()),
    *_deprecated(_RUNNER_HOME, "runner"),
    *_deprecated(_WORKSPACE_HOME, "workspace"),
    *(
        ConfigKey(f"runner[].{name}", kind, deprecated=True, replaced_by=home)
        for name, (kind, home) in _RETIRED.items()
    ),
)


def _distinct(keys: Iterable[ConfigKey]) -> tuple[ConfigKey, ...]:
    """The keys with each path once, the first declaration standing."""
    declared: dict[str, ConfigKey] = {}
    for key in keys:
        declared.setdefault(key.path, key)
    return tuple(declared.values())


HOST_REGISTRY = ConfigRegistry(
    HOST_REGISTRY_NAME,
    _distinct(_HOST_KEYS),
    (
        DurablePath(HOST_CONFIG_FILENAME, "the host's configuration"),
        DurablePath(ENV_FILENAME, "the hub tokens the host reads when the environment holds none"),
        DurablePath(DATA_DIRNAME, "the host's data directory"),
        DurablePath(f"{DATA_DIRNAME}/{HOST_STORE_FILENAME}", "the host's default store"),
        DurablePath(f"{DATA_DIRNAME}/{HOST_LOCK_FILENAME}", "the lock that keeps one host process on the directory"),
        DurablePath(HOST_SOCKET_FILENAME, "the host's local API socket"),
        DurablePath(RUNNERS_DIRNAME, "each runner's own files, by runner name"),
    ),
)
