"""Every key the host reads from ``blizzard-host.toml``, and every durable path under its runtime root.

The registry :mod:`blizzard.runner.host_config` reads through; ``blizzard-export-contracts`` commits it as
``contracts/config/host.json``. A harness section declares its own keys beside its parser
(:attr:`~blizzard.runner.harness.declaration.IHarnessSectionKind.keys`), and this module composes them under
``runner[]``. Retired and refused keys stay declared ``deprecated``: refusing one is still a read of it."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace

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
    EnvFile,
    EnvVariable,
    Lifetime,
    VariableNameRule,
)
from blizzard.foundation.runner_token_names import (
    HOST_TOKEN_VARIABLE,
    HUB_TOKEN_VARIABLE_PREFIX,
    NAME_REPLACE_PATTERN,
    NAME_REPLACEMENT,
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
    DEFAULT_TICK_SECONDS,
    DEFAULT_TRANSCRIPTS_SHIP,
    DEFAULT_WORKER_STDOUT_RETENTION_DAYS,
)
from blizzard.runner.harness.wiring import HARNESS_RUNTIME_FILES, HARNESS_SECTION_KINDS
from blizzard.runner.host_layout import (
    DATA_DIRNAME,
    ELICITATION_OUTPUT_DIRNAME,
    HOST_CONFIG_FILENAME,
    HOST_LOCK_FILENAME,
    HOST_SOCKET_FILENAME,
    HOST_STORE_FILENAME,
    LEGACY_WORKER_SCRATCH_DIRNAME,
    RUNNERS_DIRNAME,
    WORKER_SCRATCH_DIRNAME,
    WORKER_STDOUT_DIRNAME,
)
from blizzard.runner.hub.token_file import ENV_FILE_MODE, ENV_FILENAME
from blizzard.runner.loop.mode import LoopMode
from blizzard.runner.process.child_env import DEFAULT_WORKER_SHELL
from blizzard.runner.throttle.settings import DEFAULT_BUDGET_WINDOW_HOURS, DEFAULT_PACE_LEAD_PCT, HoldOrRelease
from blizzard.runner.tracing.config import RUNNER_TRACING_KEYS

HOST_REGISTRY_NAME = "host"

#: The flat runner file's keys graduation retired, with their type, where each went (``None``: nowhere), and the
#: lifetime of that home — ``None`` for a table — or ``BOOT`` for a key read only to be refused.
_RETIRED: dict[str, tuple[str, str | None, Lifetime | None]] = {
    "workspace_id": (STRING, "workspace[].id", Lifetime.IMMUTABLE),
    "workspace_root": (STRING, "workspace[].root", Lifetime.PASS),
    "workspace_provider": (STRING, "workspace[].provider", Lifetime.PASS),
    "workspace_envs": (STRING_LIST, "workspace[].envs", Lifetime.PASS),
    "workspace_repo": (ARRAY_OF_TABLES, "workspace[].repo", None),
    "workspace_prompt": (STRING, "workspace[].prompt", Lifetime.SESSION),
    "workspace_prompt_file": (STRING, "workspace[].prompt_file", Lifetime.SESSION),
    "workspace_prompt_package": (STRING, "workspace[].prompt_package", Lifetime.SESSION),
    "token_env": (STRING, None, Lifetime.BOOT),
    "runner_id": (STRING, "runner[].id", Lifetime.IMMUTABLE),
    "max_agents": (INTEGER, "runner[].agents", Lifetime.PASS),
    "external_subscription_usage": (TABLE, "subscription", None),
}
#: Keys that belong to a ``[[runner]]`` and are refused at the top level, each with its home's lifetime.
_RUNNER_HOME: tuple[tuple[str, str, Lifetime | None], ...] = (
    ("name", STRING, Lifetime.PASS),
    ("projects", ARRAY_OF_TABLES, None),
    ("gates", STRING_LIST, Lifetime.PASS),
    ("runner_prompt", STRING, Lifetime.SESSION),
    ("runner_prompt_file", STRING, Lifetime.SESSION),
    ("transcripts_root", STRING, Lifetime.SESSION),
    ("harness", TABLE, None),
    *((kind.table, TABLE, None) for kind in HARNESS_SECTION_KINDS),
    ("models", TABLE, None),
    ("effort", TABLE, None),
    ("cost", TABLE, None),
    ("queue", TABLE, None),
    ("context", TABLE, None),
    ("worker", TABLE, None),
    ("harness_binary", STRING, Lifetime.SESSION),
    ("harness_permission_mode", STRING, Lifetime.SESSION),
    ("worker_settings_path", STRING, Lifetime.BOOT),
    ("claude_code_credentials_path", STRING, Lifetime.BOOT),
)
RUNNER_HOME_KEYS = tuple(name for name, _, _ in _RUNNER_HOME)
#: Keys that belong to a ``[[workspace]]`` and are refused at the top level, each with its home's lifetime.
_WORKSPACE_HOME: tuple[tuple[str, str, Lifetime | None], ...] = (
    ("root", STRING, Lifetime.PASS),
    ("provider", STRING, Lifetime.PASS),
    ("envs", STRING_LIST, Lifetime.PASS),
    ("repo", ARRAY_OF_TABLES, None),
    ("prompt", STRING, Lifetime.SESSION),
    ("prompt_file", STRING, Lifetime.SESSION),
    ("prompt_package", STRING, Lifetime.SESSION),
    ("max_environments", INTEGER, Lifetime.PASS),
    ("base_branch", STRING, Lifetime.PASS),
)


def _prefixed(prefix: str, keys: Iterable[ConfigKey]) -> tuple[ConfigKey, ...]:
    return tuple(
        replace(
            key,
            path=f"{prefix}.{key.path}",
            replaced_by=None if key.replaced_by is None else f"{prefix}.{key.replaced_by}",
        )
        for key in keys
    )


def _deprecated(entries: Iterable[tuple[str, str, Lifetime | None]], home: str) -> tuple[ConfigKey, ...]:
    return tuple(
        ConfigKey(name, kind, deprecated=True, replaced_by=f"{home}[].{name}", lifetime=lifetime)
        for name, kind, lifetime in entries
    )


_HOST_KEYS = (
    ConfigKey("hub_url", STRING, default=DEFAULT_HUB_URL, lifetime=Lifetime.BOOT),
    ConfigKey("host", STRING, default=DEFAULT_HOST, lifetime=Lifetime.BOOT),
    ConfigKey("port", INTEGER, default=DEFAULT_PORT, lifetime=Lifetime.BOOT),
    ConfigKey("loop", STRING, default=LoopMode.TIMED.value, lifetime=Lifetime.BOOT),
    ConfigKey("db_url", STRING, lifetime=Lifetime.BOOT),
    ConfigKey("public_url", STRING_OR_LIST, lifetime=Lifetime.BOOT),
    ConfigKey("trusted_proxies", STRING_LIST, lifetime=Lifetime.BOOT),
    ConfigKey("session_secret_env", STRING, default=DEFAULT_SESSION_SECRET_ENV, lifetime=Lifetime.BOOT),
    ConfigKey("host_token", STRING, lifetime=Lifetime.BOOT, env=HOST_TOKEN_VARIABLE),
    ConfigKey("tick_seconds", NUMBER, default=DEFAULT_TICK_SECONDS, lifetime=Lifetime.PASS),
    ConfigKey("hub_tokens", OPEN_TABLE, lifetime=Lifetime.BOOT),
    ConfigKey("flags", OPEN_TABLE, lifetime=Lifetime.BOOT),
    ConfigKey("auth", TABLE),
    ConfigKey("auth.superuser", STRING, lifetime=Lifetime.BOOT),
    ConfigKey("auth.hub_role_default", STRING, default=DEFAULT_AUTH_HUB_ROLE, lifetime=Lifetime.BOOT),
    ConfigKey("auth.users", OPEN_TABLE, lifetime=Lifetime.BOOT),
    *TRACING_KEYS,
    *RUNNER_TRACING_KEYS,
    ConfigKey("transcripts", TABLE),
    ConfigKey("transcripts.ship", BOOLEAN, default=DEFAULT_TRANSCRIPTS_SHIP, lifetime=Lifetime.PASS),
    ConfigKey("transcripts.record_max_bytes", INTEGER, lifetime=Lifetime.PASS),
    ConfigKey("transcripts.chunk_max_bytes", INTEGER, lifetime=Lifetime.PASS),
    ConfigKey("worker_stdout", TABLE),
    ConfigKey(
        "worker_stdout.retention_days", INTEGER, default=DEFAULT_WORKER_STDOUT_RETENTION_DAYS, lifetime=Lifetime.PASS
    ),
    ConfigKey("worker_scratch", TABLE),
    ConfigKey("worker_scratch.root", STRING, lifetime=Lifetime.BOOT),
    ConfigKey("tools", TABLE),
    ConfigKey("tools.path_prepend", STRING_LIST, default=(), lifetime=Lifetime.SESSION),
    ConfigKey("tools.env_passthrough", STRING_LIST, default=(), lifetime=Lifetime.SESSION),
    ConfigKey("subscription", ARRAY_OF_TABLES),
    ConfigKey("subscription[].slug", STRING, required=True, lifetime=Lifetime.IMMUTABLE),
    ConfigKey("subscription[].name", STRING, required=True, lifetime=Lifetime.PASS),
    ConfigKey("subscription[].provider", STRING, required=True, lifetime=Lifetime.IMMUTABLE),
    ConfigKey("subscription[].credentials_path", STRING, lifetime=Lifetime.PASS),
    ConfigKey("subscription[].usage_api_base", STRING, lifetime=Lifetime.PASS),
    ConfigKey(
        "subscription[].sample_interval_seconds",
        INTEGER,
        default=DEFAULT_EXTERNAL_USAGE_SAMPLE_INTERVAL_SECONDS,
        lifetime=Lifetime.PASS,
    ),
    ConfigKey("workspace", ARRAY_OF_TABLES),
    ConfigKey("workspace[].id", STRING, required=True, lifetime=Lifetime.IMMUTABLE),
    ConfigKey("workspace[].root", STRING, default="", lifetime=Lifetime.PASS),
    ConfigKey("workspace[].provider", STRING, default="winter", lifetime=Lifetime.PASS),
    ConfigKey("workspace[].envs", STRING_LIST, default=DEFAULT_ENV_POOL, lifetime=Lifetime.PASS),
    ConfigKey("workspace[].max_environments", INTEGER, default=DEFAULT_MAX_ENVIRONMENTS, lifetime=Lifetime.PASS),
    ConfigKey("workspace[].repo", ARRAY_OF_TABLES),
    ConfigKey("workspace[].repo[].name", STRING, required=True, lifetime=Lifetime.PASS),
    ConfigKey("workspace[].repo[].url", STRING, required=True, lifetime=Lifetime.PASS),
    ConfigKey("workspace[].base_branch", STRING, default=DEFAULT_BASE_BRANCH, lifetime=Lifetime.PASS),
    ConfigKey("workspace[].prompt", STRING, default="", lifetime=Lifetime.SESSION),
    ConfigKey("workspace[].prompt_file", STRING, default="", lifetime=Lifetime.SESSION),
    ConfigKey("workspace[].prompt_package", STRING, default="", lifetime=Lifetime.SESSION),
    ConfigKey("runner", ARRAY_OF_TABLES),
    ConfigKey("runner[].id", STRING, lifetime=Lifetime.IMMUTABLE),
    ConfigKey("runner[].name", STRING, required=True, lifetime=Lifetime.PASS),
    ConfigKey("runner[].subscription", STRING, lifetime=Lifetime.PASS),
    ConfigKey("runner[].projects", ARRAY_OF_TABLES, required=True),
    ConfigKey("runner[].projects[].project", STRING, required=True, lifetime=Lifetime.PASS),
    ConfigKey("runner[].projects[].workspace", STRING, required=True, lifetime=Lifetime.PASS),
    ConfigKey("runner[].agents", INTEGER, lifetime=Lifetime.PASS),
    ConfigKey(
        "runner[].projects[].agents", INTEGER, deprecated=True, replaced_by="runner[].agents", lifetime=Lifetime.PASS
    ),
    ConfigKey("runner[].gates", STRING_LIST, default=(), lifetime=Lifetime.PASS),
    ConfigKey("runner[].runner_prompt", STRING, default="", lifetime=Lifetime.SESSION),
    ConfigKey("runner[].runner_prompt_file", STRING, default="", lifetime=Lifetime.SESSION),
    ConfigKey("runner[].transcripts_root", STRING, default="", lifetime=Lifetime.SESSION),
    ConfigKey("runner[].harness", TABLE),
    ConfigKey("runner[].harness.autonomy", STRING, lifetime=Lifetime.SESSION),
    ConfigKey("runner[].harness.config_dir", STRING, lifetime=Lifetime.SESSION),
    ConfigKey("runner[].worker", TABLE),
    ConfigKey("runner[].worker.env_passthrough", STRING_LIST, default=(), lifetime=Lifetime.SESSION),
    ConfigKey("runner[].worker.path_prepend", STRING_LIST, default=(), lifetime=Lifetime.SESSION),
    ConfigKey("runner[].worker.shell", STRING, default=DEFAULT_WORKER_SHELL, lifetime=Lifetime.SESSION),
    ConfigKey("runner[].queue", TABLE),
    ConfigKey("runner[].queue.strict", BOOLEAN, default=DEFAULT_QUEUE_STRICT, lifetime=Lifetime.PASS),
    ConfigKey("runner[].cost", TABLE),
    ConfigKey("runner[].cost.chunk_cap_usd", NUMBER, lifetime=Lifetime.PASS),
    ConfigKey("runner[].cost.runner_ceiling_usd", NUMBER, lifetime=Lifetime.PASS),
    ConfigKey(
        "runner[].cost.window_hours", NUMBER, default=DEFAULT_RUNNER_CEILING_WINDOW_HOURS, lifetime=Lifetime.PASS
    ),
    ConfigKey("runner[].throttle", TABLE),
    ConfigKey("runner[].throttle.pace", TABLE),
    ConfigKey("runner[].throttle.pace.window", STRING, lifetime=Lifetime.PASS),
    ConfigKey("runner[].throttle.pace.lead_pct", NUMBER, default=DEFAULT_PACE_LEAD_PCT, lifetime=Lifetime.PASS),
    ConfigKey("runner[].throttle.pace.taper", ARRAY_OF_TABLES),
    ConfigKey("runner[].throttle.pace.taper[].from_pct", NUMBER, lifetime=Lifetime.PASS),
    ConfigKey("runner[].throttle.pace.taper[].lead_pct", NUMBER, lifetime=Lifetime.PASS),
    ConfigKey("runner[].throttle.utilization", TABLE),
    ConfigKey("runner[].throttle.utilization.below_pct", NUMBER, lifetime=Lifetime.PASS),
    ConfigKey("runner[].throttle.utilization.windows", STRING_LIST, lifetime=Lifetime.PASS),
    ConfigKey("runner[].throttle.budget", TABLE),
    ConfigKey("runner[].throttle.budget.usd", NUMBER, lifetime=Lifetime.PASS),
    ConfigKey(
        "runner[].throttle.budget.window_hours", NUMBER, default=DEFAULT_BUDGET_WINDOW_HOURS, lifetime=Lifetime.PASS
    ),
    ConfigKey("runner[].throttle.schedule", TABLE),
    ConfigKey("runner[].throttle.schedule.days", STRING_LIST, lifetime=Lifetime.PASS),
    ConfigKey("runner[].throttle.schedule.hours", STRING_LIST, lifetime=Lifetime.PASS),
    ConfigKey("runner[].throttle.on_decline", STRING, default=HoldOrRelease.HOLD.value, lifetime=Lifetime.PASS),
    ConfigKey("runner[].throttle.on_usage_limit", STRING, default=HoldOrRelease.HOLD.value, lifetime=Lifetime.PASS),
    ConfigKey("runner[].context", TABLE),
    ConfigKey("runner[].context.warn_tokens", INTEGER, lifetime=Lifetime.PASS),
    ConfigKey(
        "runner[].context.sample_interval_seconds",
        INTEGER,
        default=DEFAULT_CONTEXT_SAMPLE_INTERVAL_SECONDS,
        lifetime=Lifetime.PASS,
    ),
    *_prefixed("runner[]", (key for kind in HARNESS_SECTION_KINDS for key in kind.keys)),
    *(
        ConfigKey(name, kind, deprecated=True, replaced_by=home, lifetime=lifetime)
        for name, (kind, home, lifetime) in _RETIRED.items()
    ),
    *_deprecated(_RUNNER_HOME, "runner"),
    *_deprecated(_WORKSPACE_HOME, "workspace"),
    *(
        ConfigKey(f"runner[].{name}", kind, deprecated=True, replaced_by=home, lifetime=lifetime)
        for name, (kind, home, lifetime) in _RETIRED.items()
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
        DurablePath(
            ENV_FILENAME,
            "the hub tokens the host reads when the environment holds none",
            written_by=("add", "host", "ingest", "init"),
            mode=ENV_FILE_MODE,
        ),
        DurablePath(DATA_DIRNAME, "the host's data directory"),
        DurablePath(f"{DATA_DIRNAME}/{HOST_STORE_FILENAME}", "the host's default store"),
        DurablePath(f"{DATA_DIRNAME}/{HOST_LOCK_FILENAME}", "the lock that keeps one host process on the directory"),
        DurablePath(HOST_SOCKET_FILENAME, "the host's local API socket"),
        DurablePath(RUNNERS_DIRNAME, "each runner's own files, by runner name"),
        DurablePath(
            f"{RUNNERS_DIRNAME}/<name>",
            "one runner's own directory, named for the runner",
            written_by=("add", "ingest"),
        ),
        DurablePath(WORKER_STDOUT_DIRNAME, "each worker's harness stdout, one file per lease generation"),
        DurablePath(ELICITATION_OUTPUT_DIRNAME, "each elicitation's output, one file per attempt"),
        DurablePath(WORKER_SCRATCH_DIRNAME, "worker scratch root (default; overridden by worker_scratch.root)"),
        DurablePath(
            f"{WORKER_SCRATCH_DIRNAME}/r-<tail>/l-<tail>", "compact per-lease worker scratch (BLIZZARD_TMPDIR)"
        ),
        DurablePath(f"{WORKER_SCRATCH_DIRNAME}/.lock", "scratch root allocation and deletion lock", mode=0o600),
        DurablePath(f"{WORKER_SCRATCH_DIRNAME}/.owners", "scratch allocation ownership records", mode=0o700),
        DurablePath(
            f"{WORKER_SCRATCH_DIRNAME}/.owners/r-<tail>.json", "full runner identity for a compact runner", mode=0o600
        ),
        DurablePath(
            f"{WORKER_SCRATCH_DIRNAME}/.owners/r-<tail>", "lease ownership records for a compact runner", mode=0o700
        ),
        DurablePath(
            f"{WORKER_SCRATCH_DIRNAME}/.owners/r-<tail>/l-<tail>.json",
            "full lease identity for a compact allocation",
            mode=0o600,
        ),
        DurablePath(LEGACY_WORKER_SCRATCH_DIRNAME, "legacy worker scratch retained for upgrade cleanup"),
        DurablePath(
            f"{LEGACY_WORKER_SCRATCH_DIRNAME}/<runner-id>", "legacy runner scratch retained for upgrade cleanup"
        ),
        DurablePath(f"{DATA_DIRNAME}/worker-scratch-root", "host's effective worker scratch root binding", mode=0o600),
        *(DurablePath(path, holds, written_by=("init",)) for path, holds in HARNESS_RUNTIME_FILES),
    ),
    EnvFile(
        ENV_FILENAME,
        ENV_FILE_MODE,
        atomic=True,
        variables=(
            EnvVariable(HOST_TOKEN_VARIABLE, "the host's one host token"),
            EnvVariable(
                f"{HUB_TOKEN_VARIABLE_PREFIX}<NAME>",
                "one runner's hub token, by runner name",
                VariableNameRule(HUB_TOKEN_VARIABLE_PREFIX, True, NAME_REPLACE_PATTERN, NAME_REPLACEMENT),
            ),
        ),
    ),
)
