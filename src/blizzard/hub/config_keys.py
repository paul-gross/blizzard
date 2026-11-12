"""Every key the hub reads from ``blizzard-hub.toml``, and every durable path under its runtime root.

Each key is declared once here, with its type. ``docs/backup.md`` owns the prose behind each durable path."""

from __future__ import annotations

from blizzard.foundation.config_keys import (
    ARRAY_OF_TABLES,
    BOOLEAN,
    INTEGER,
    NUMBER,
    OPEN_TABLE,
    STRING,
    STRING_LIST,
    TABLE,
    ConfigKey,
    ConfigRegistry,
    DurablePath,
    Lifetime,
)
from blizzard.foundation.trace_export.config import TRACING_KEYS
from blizzard.hub.auth.throttle import DEFAULT_CAPACITY, DEFAULT_REFILL_SECONDS
from blizzard.hub.config_defaults import (
    AUTH_MODE_NONE,
    DEFAULT_ACTION_PASS_SECONDS,
    DEFAULT_ANNOTATION_INTERVAL_SECONDS,
    DEFAULT_CLOSE_FORGE_WRITES_ENABLED,
    DEFAULT_FOLLOW_LATEST,
    DEFAULT_HOST,
    DEFAULT_PORT,
    ENV_DB_URL,
    ENV_HOST,
    ENV_PORT,
)
from blizzard.hub.domain.execution.auth.produces import PRODUCES_WARN
from blizzard.hub.domain.execution.auth.route import ROUTE_TOKEN_WARN
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.domain.sweeps.names import SweepMode, SweepName
from blizzard.hub.layout import (
    CONFIG_FILENAME,
    DATA_DIRNAME,
    SECRET_KEYS_PARTS,
    SIGNING_KEYS_PARTS,
    STORE_FILENAME,
)

HUB_REGISTRY_NAME = "hub"

_EGRESS = EgressConfig()

#: The record kind that replaced ``[[work_source]]`` blocks: a work source is configured through the API now.
WORK_SOURCE_RECORDS = "work source records"

HUB_REGISTRY = ConfigRegistry(
    HUB_REGISTRY_NAME,
    (
        ConfigKey("db_url", STRING, lifetime=Lifetime.BOOT, env=ENV_DB_URL),
        ConfigKey("host", STRING, default=DEFAULT_HOST, lifetime=Lifetime.BOOT, env=ENV_HOST),
        ConfigKey("port", INTEGER, default=DEFAULT_PORT, lifetime=Lifetime.BOOT, env=ENV_PORT),
        ConfigKey("route_token_mode", STRING, default=ROUTE_TOKEN_WARN, lifetime=Lifetime.PASS),
        ConfigKey("produces_mode", STRING, default=PRODUCES_WARN, lifetime=Lifetime.PASS),
        ConfigKey("follow_latest", BOOLEAN, default=DEFAULT_FOLLOW_LATEST, lifetime=Lifetime.BOOT),
        ConfigKey(
            "annotation_interval_seconds", INTEGER, default=DEFAULT_ANNOTATION_INTERVAL_SECONDS, lifetime=Lifetime.PASS
        ),
        ConfigKey("action_pass_seconds", INTEGER, default=DEFAULT_ACTION_PASS_SECONDS, lifetime=Lifetime.PASS),
        ConfigKey(
            "close_forge_writes_enabled", BOOLEAN, default=DEFAULT_CLOSE_FORGE_WRITES_ENABLED, lifetime=Lifetime.PASS
        ),
        ConfigKey("trusted_proxies", STRING_LIST, lifetime=Lifetime.BOOT),
        ConfigKey("public_url", STRING, lifetime=Lifetime.BOOT),
        ConfigKey("flags", OPEN_TABLE, lifetime=Lifetime.BOOT),
        ConfigKey("auth", TABLE),
        ConfigKey("auth.mode", STRING, default=AUTH_MODE_NONE, lifetime=Lifetime.BOOT),
        ConfigKey("auth.superuser", STRING, lifetime=Lifetime.BOOT),
        ConfigKey("auth.login_throttle", TABLE),
        ConfigKey("auth.login_throttle.enabled", BOOLEAN, default=True, lifetime=Lifetime.PASS),
        ConfigKey("auth.login_throttle.capacity", INTEGER, default=DEFAULT_CAPACITY, lifetime=Lifetime.PASS),
        ConfigKey("auth.login_throttle.refill_seconds", NUMBER, default=DEFAULT_REFILL_SECONDS, lifetime=Lifetime.PASS),
        ConfigKey("auth.oauth", TABLE),
        ConfigKey("auth.oauth.provider", ARRAY_OF_TABLES),
        ConfigKey("auth.oauth.provider[].name", STRING, required=True, lifetime=Lifetime.BOOT),
        ConfigKey("auth.oauth.provider[].type", STRING, required=True, lifetime=Lifetime.BOOT),
        ConfigKey("auth.oauth.provider[].display_name", STRING, required=True, lifetime=Lifetime.BOOT),
        ConfigKey("auth.oauth.provider[].client_id", STRING, required=True, lifetime=Lifetime.BOOT),
        ConfigKey("auth.oauth.provider[].client_secret_env", STRING, required=True, lifetime=Lifetime.BOOT),
        ConfigKey("auth.oauth.provider[].issuer", STRING, lifetime=Lifetime.BOOT),
        ConfigKey("auth.oauth.provider[].api_base", STRING, lifetime=Lifetime.BOOT),
        ConfigKey("auth.oauth.provider[].emails_authoritative", BOOLEAN, default=False, lifetime=Lifetime.BOOT),
        ConfigKey("transcripts", TABLE),
        ConfigKey("transcripts.record_max_bytes", INTEGER, lifetime=Lifetime.PASS),
        ConfigKey("transcripts.chunk_budget_max_bytes", INTEGER, lifetime=Lifetime.PASS),
        ConfigKey("transcripts.runner_daily_rate_max_bytes", INTEGER, lifetime=Lifetime.PASS),
        *TRACING_KEYS,
        # Runner-only keys the hub never read: declared so an existing file's key resolves, refused at load.
        ConfigKey(
            "tracing.worker_programs",
            BOOLEAN,
            default=False,
            deprecated=True,
            replaced_by="tracing.worker_programs in blizzard-host.toml",
            lifetime=Lifetime.SESSION,
        ),
        ConfigKey(
            "tracing.worker_program_services",
            OPEN_TABLE,
            deprecated=True,
            replaced_by="tracing.worker_program_services in blizzard-host.toml",
            lifetime=Lifetime.SESSION,
        ),
        ConfigKey(
            "tracing.harness_telemetry",
            BOOLEAN,
            default=False,
            deprecated=True,
            replaced_by="tracing.harness_telemetry in blizzard-host.toml",
            lifetime=Lifetime.SESSION,
        ),
        ConfigKey("egress", TABLE),
        ConfigKey("egress.directory", STRING, lifetime=Lifetime.BOOT),
        ConfigKey("egress.format", STRING, default=_EGRESS.format, lifetime=Lifetime.BOOT),
        ConfigKey("egress.datasets", STRING_LIST, default=_EGRESS.datasets, lifetime=Lifetime.PASS),
        ConfigKey("egress.sweep_seconds", INTEGER, default=_EGRESS.sweep_seconds, lifetime=Lifetime.PASS),
        ConfigKey("egress.settle_seconds", INTEGER, default=_EGRESS.settle_seconds, lifetime=Lifetime.PASS),
        ConfigKey("egress.batch_limit", INTEGER, default=_EGRESS.batch_limit, lifetime=Lifetime.PASS),
        ConfigKey("egress.max_rows_per_file", INTEGER, default=_EGRESS.max_rows_per_file, lifetime=Lifetime.PASS),
        ConfigKey("egress.min_free_bytes", INTEGER, default=_EGRESS.min_free_bytes, lifetime=Lifetime.PASS),
        ConfigKey("egress.backfill_max_window", INTEGER, default=_EGRESS.backfill_max_window, lifetime=Lifetime.PASS),
        ConfigKey("egress.file_paths", STRING, default=_EGRESS.file_paths, lifetime=Lifetime.BOOT),
        ConfigKey("egress.path_key_env", STRING, default=_EGRESS.path_key_env, lifetime=Lifetime.BOOT),
        ConfigKey("egress.extractor_versions", STRING, default=_EGRESS.extractor_versions, lifetime=Lifetime.PASS),
        ConfigKey("work_source", ARRAY_OF_TABLES, deprecated=True, replaced_by=WORK_SOURCE_RECORDS),
        ConfigKey("work_source[].name", STRING, required=True, lifetime=Lifetime.BOOT),
        ConfigKey("work_source[].provider", STRING, required=True, lifetime=Lifetime.BOOT),
        ConfigKey("work_source[].repo", STRING, required=True, lifetime=Lifetime.BOOT),
        ConfigKey("work_source[].token_env", STRING, required=True, lifetime=Lifetime.BOOT),
        ConfigKey("work_source[].annotate", BOOLEAN, default=False, lifetime=Lifetime.BOOT),
        ConfigKey("work_source[].api_base", STRING, lifetime=Lifetime.BOOT),
        ConfigKey("work_source[].web_base", STRING, lifetime=Lifetime.BOOT),
        ConfigKey("work_source[].close", BOOLEAN, deprecated=True, lifetime=Lifetime.BOOT),
        ConfigKey("pm_source", ARRAY_OF_TABLES, deprecated=True, replaced_by="work_source"),
        ConfigKey("sweeps", TABLE),
        *(
            key
            for name in SweepName
            for key in (
                ConfigKey(f"sweeps.{name}", TABLE),
                ConfigKey(f"sweeps.{name}.mode", STRING, default=SweepMode.TIMED.value, lifetime=Lifetime.BOOT),
            )
        ),
    ),
    (
        DurablePath(CONFIG_FILENAME, "the hub's configuration"),
        DurablePath(f"{DATA_DIRNAME}/{STORE_FILENAME}", "the default store, with its -wal sidecar when one exists"),
        DurablePath("/".join((DATA_DIRNAME, *SIGNING_KEYS_PARTS)), "the IdP signing keypairs and their meta.json"),
        DurablePath("/".join((DATA_DIRNAME, *SECRET_KEYS_PARTS)), "the keys that seal every stored secret"),
    ),
)
