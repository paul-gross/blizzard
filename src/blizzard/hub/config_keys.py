"""Every key the hub reads from ``blizzard-hub.toml``, and every durable path under its runtime root.

The registry :mod:`blizzard.hub.config` reads through; ``blizzard-export-contracts`` commits it as
``contracts/config/hub.json``. ``docs/backup.md`` owns the prose behind each durable path."""

from __future__ import annotations

from blizzard.foundation.config_keys import (
    ARRAY_OF_TABLES,
    BOOLEAN,
    INTEGER,
    OPEN_TABLE,
    STRING,
    STRING_LIST,
    TABLE,
    ConfigKey,
    ConfigRegistry,
    DurablePath,
)
from blizzard.foundation.trace_export.config import TRACING_KEYS
from blizzard.hub.config_defaults import (
    AUTH_MODE_NONE,
    DEFAULT_ANNOTATION_INTERVAL_SECONDS,
    DEFAULT_CLOSE_FORGE_WRITES_ENABLED,
    DEFAULT_FOLLOW_LATEST,
    DEFAULT_HOST,
    DEFAULT_PORT,
)
from blizzard.hub.domain.execution.auth.produces import PRODUCES_WARN
from blizzard.hub.domain.execution.auth.route import ROUTE_TOKEN_WARN
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.layout import (
    CONFIG_FILENAME,
    DATA_DIRNAME,
    SECRET_KEYS_PARTS,
    SIGNING_KEYS_PARTS,
    STORE_FILENAME,
    WORKDIRS_DIRNAME,
)

HUB_REGISTRY_NAME = "hub"

_EGRESS = EgressConfig()

HUB_REGISTRY = ConfigRegistry(
    HUB_REGISTRY_NAME,
    (
        ConfigKey("db_url", STRING),
        ConfigKey("host", STRING, default=DEFAULT_HOST),
        ConfigKey("port", INTEGER, default=DEFAULT_PORT),
        ConfigKey("route_token_mode", STRING, default=ROUTE_TOKEN_WARN),
        ConfigKey("produces_mode", STRING, default=PRODUCES_WARN),
        ConfigKey("follow_latest", BOOLEAN, default=DEFAULT_FOLLOW_LATEST),
        ConfigKey("annotation_interval_seconds", INTEGER, default=DEFAULT_ANNOTATION_INTERVAL_SECONDS),
        ConfigKey("close_forge_writes_enabled", BOOLEAN, default=DEFAULT_CLOSE_FORGE_WRITES_ENABLED),
        ConfigKey("trusted_proxies", STRING_LIST),
        ConfigKey("public_url", STRING),
        ConfigKey("flags", OPEN_TABLE),
        ConfigKey("auth", TABLE),
        ConfigKey("auth.mode", STRING, default=AUTH_MODE_NONE),
        ConfigKey("auth.superuser", STRING),
        ConfigKey("auth.oauth", TABLE),
        ConfigKey("auth.oauth.provider", ARRAY_OF_TABLES),
        ConfigKey("auth.oauth.provider[].name", STRING, required=True),
        ConfigKey("auth.oauth.provider[].type", STRING, required=True),
        ConfigKey("auth.oauth.provider[].display_name", STRING, required=True),
        ConfigKey("auth.oauth.provider[].client_id", STRING, required=True),
        ConfigKey("auth.oauth.provider[].client_secret_env", STRING, required=True),
        ConfigKey("auth.oauth.provider[].issuer", STRING),
        ConfigKey("auth.oauth.provider[].api_base", STRING),
        ConfigKey("auth.oauth.provider[].emails_authoritative", BOOLEAN, default=False),
        ConfigKey("transcripts", TABLE),
        ConfigKey("transcripts.record_max_bytes", INTEGER),
        ConfigKey("transcripts.chunk_budget_max_bytes", INTEGER),
        ConfigKey("transcripts.runner_daily_rate_max_bytes", INTEGER),
        *TRACING_KEYS,
        ConfigKey("egress", TABLE),
        ConfigKey("egress.directory", STRING),
        ConfigKey("egress.format", STRING, default=_EGRESS.format),
        ConfigKey("egress.datasets", STRING_LIST, default=_EGRESS.datasets),
        ConfigKey("egress.sweep_seconds", INTEGER, default=_EGRESS.sweep_seconds),
        ConfigKey("egress.settle_seconds", INTEGER, default=_EGRESS.settle_seconds),
        ConfigKey("egress.batch_limit", INTEGER, default=_EGRESS.batch_limit),
        ConfigKey("egress.max_rows_per_file", INTEGER, default=_EGRESS.max_rows_per_file),
        ConfigKey("egress.min_free_bytes", INTEGER, default=_EGRESS.min_free_bytes),
        ConfigKey("egress.backfill_max_window", INTEGER, default=_EGRESS.backfill_max_window),
        ConfigKey("egress.file_paths", STRING, default=_EGRESS.file_paths),
        ConfigKey("egress.path_key_env", STRING, default=_EGRESS.path_key_env),
        ConfigKey("egress.extractor_versions", STRING, default=_EGRESS.extractor_versions),
        ConfigKey("work_source", ARRAY_OF_TABLES),
        ConfigKey("work_source[].name", STRING, required=True),
        ConfigKey("work_source[].provider", STRING, required=True),
        ConfigKey("work_source[].repo", STRING, required=True),
        ConfigKey("work_source[].token_env", STRING, required=True),
        ConfigKey("work_source[].annotate", BOOLEAN, default=False),
        ConfigKey("work_source[].api_base", STRING),
        ConfigKey("work_source[].web_base", STRING),
        ConfigKey("work_source[].close", BOOLEAN, deprecated=True),
        ConfigKey("pm_source", ARRAY_OF_TABLES, deprecated=True, replaced_by="work_source"),
    ),
    (
        DurablePath(CONFIG_FILENAME, "the hub's configuration"),
        DurablePath(f"{DATA_DIRNAME}/{STORE_FILENAME}", "the default store, with its -wal sidecar when one exists"),
        DurablePath("/".join((DATA_DIRNAME, *SIGNING_KEYS_PARTS)), "the IdP signing keypairs and their meta.json"),
        DurablePath("/".join((DATA_DIRNAME, *SECRET_KEYS_PARTS)), "the keys that seal every stored secret"),
        DurablePath(f"{DATA_DIRNAME}/{WORKDIRS_DIRNAME}", "scratch clones a hub command node uses; reclaimable"),
    ),
)
