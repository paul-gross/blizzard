"""Hub runtime configuration — resolved from a runtime directory.

The store URL is the single portability knob (``bzh:sql-portable``): the sqlite
default lives under the data dir, and postgres is the same config with a different
URL. The bind port falls back to ``BZ_HUB_PORT``. There is no stdlib TOML writer, so
:meth:`HubConfig.to_toml` hand-rolls the emit."""

from __future__ import annotations

import json
import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, overload
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url

from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.roles import domain_model, dto
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.hub.domain.observability.transcripts import TranscriptCaps

CONFIG_FILENAME = "blizzard-hub.toml"
DATA_DIRNAME = "data"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8421

ENV_HOST = "BZ_HUB_HOST"
ENV_PORT = "BZ_HUB_PORT"
# Varies the store URL by environment rather than baking one per image; honored
# identically by every verb, which all resolve through `load` (`bzh:sql-portable`).
ENV_DB_URL = "BZ_HUB_DB_URL"

# The runner-identity rollout brake — `warn` logs a missing/invalid bearer
# token and proceeds; `enforce` rejects. Defaults to `warn` so tokens can enroll first.
RUNNER_AUTH_WARN = "warn"
RUNNER_AUTH_ENFORCE = "enforce"
_KNOWN_RUNNER_AUTH_MODES = {RUNNER_AUTH_WARN, RUNNER_AUTH_ENFORCE}

# The route-capability-token rollout brake, separate from `runner_auth_mode`
# so the two enforce independently — `warn` proceeds; `enforce` rejects before the fence.
ROUTE_TOKEN_WARN = "warn"
ROUTE_TOKEN_ENFORCE = "enforce"
_KNOWN_ROUTE_TOKEN_MODES = {ROUTE_TOKEN_WARN, ROUTE_TOKEN_ENFORCE}

# The produces-artifact rollout brake, separate from the two above — `warn`
# logs a `produces:` name with no attachment and proceeds; `enforce` rejects it.
PRODUCES_WARN = "warn"
PRODUCES_ENFORCE = "enforce"
_KNOWN_PRODUCES_MODES = {PRODUCES_WARN, PRODUCES_ENFORCE}

# The only work-source provider grammar a source may declare; an unknown provider fails
# at config load, not at first use.
KNOWN_WORK_SOURCE_PROVIDERS = {"github"}
_REQUIRED_WORK_SOURCE_KEYS = ("name", "provider", "repo", "token_env")

# The built-in, always-seated hub work source's reserved name — no
# `[[work_source]]` entry may claim it.
RESERVED_HUB_SOURCE_NAME = "hub"

# `[[work_source]]`'s pre-rename name — deliberately *not* aliased; pinned by
# `test_config.py::test_a_leftover_pm_source_block_fails_the_load_naming_the_new_key`.
RENAMED_WORK_SOURCE_KEY = "pm_source"

# The human-auth rollout knob — `none` (the default) resolves every request
# to an implicit identity with no store read; `oauth` activates the session seam.
AUTH_MODE_NONE = "none"
AUTH_MODE_OAUTH = "oauth"
_KNOWN_AUTH_MODES = {AUTH_MODE_NONE, AUTH_MODE_OAUTH}

# `[[auth.oauth.provider]]` required keys — structural presence only;
# secret resolution and `type`/`issuer` validation happen where a provider is consumed.
_REQUIRED_OAUTH_PROVIDER_KEYS = ("name", "type", "display_name", "client_id", "client_secret_env")

# A fresh scaffold has no configured external source, so `to_toml()` emits this as a
# comment rather than leaving the block undiscoverable.
_WORK_SOURCE_EXAMPLE_COMMENT = """
# Uncomment and edit to configure an external work source — the built-in `hub` source
# is always seated, so `work-items` never 503s, but a chunk pointing at an external
# forge issue needs its own [[work_source]] before that pointer's label resolves.
#
# [[work_source]]
# name = "blizzard"          # names this source; ingest tokens and board labels key on it
# provider = "github"        # the only adapter grammar that exists today
# repo = "owner/name"        # the "owner/repo" this source is pinned to
# token_env = "BZ_WORK_SOURCE_TOKEN"  # names an env var — the secret itself lives in this
#                                      # runtime's env file (e.g. /etc/blizzard/hub.env), never here
# annotate = false            # opt into the forge-status label sweep; only the canonical
#                              # instance for a repo should ever set this to true — two
#                              # writers against the same forge repo will fight
# api_base = "https://ghe.example.internal/api/v3"  # optional: override the API origin (e.g. GHE)
# web_base = "https://ghe.example.internal"          # optional: override the web origin; derives from api_base
"""

# Mirrors `_WORK_SOURCE_EXAMPLE_COMMENT` — emitted when `[auth]` carries no configured
# login provider, so the block stays discoverable under `mode = "none"`.
_AUTH_OAUTH_PROVIDER_EXAMPLE_COMMENT = """
# Uncomment and edit to declare an OAuth login provider — consumed once `mode =
# "oauth"` and a login mechanism exist; parsed-and-carried here so the
# config schema is stable ahead of that.
#
# [[auth.oauth.provider]]
# name = "github"                    # the provider's identity; identities key on it
# type = "github"                    # "github" or "oidc"
# display_name = "GitHub"            # the login button's label
# client_id = "..."                  # the OAuth app's client id
# client_secret_env = "BZ_OAUTH_GITHUB_SECRET"  # names an env var — the secret itself
#                                                 # lives in this runtime's env file
# issuer = "https://accounts.example.com"        # oidc only: the discovery issuer
# api_base = "https://ghe.example.internal"       # optional: override the provider's
#                                                  # default host (github type only)
"""


class ConfigError(RuntimeError):
    """A runtime directory is missing its config — it was never initialized."""


@domain_model
@dataclass(frozen=True)
class Env:
    """The environment overrides ``scaffold`` and ``load`` both resolve through, so a
    container's very first boot and every later one fail identically on a bad value."""

    values: Mapping[str, str] = field(default_factory=lambda: os.environ)

    @overload
    def text(self, name: str) -> str | None: ...

    @overload
    def text(self, name: str, default: str) -> str: ...

    def text(self, name: str, default: str | None = None) -> str | None:
        return self.values.get(name, default)

    def port(self, fallback: int) -> int:
        """The bind port, naming the variable on a malformed value."""
        raw = self.values.get(ENV_PORT)
        if raw is None:
            return fallback
        try:
            return int(raw)
        except ValueError as exc:
            raise ConfigError(f"{ENV_PORT} must be an integer, got {raw!r}") from exc


@domain_model
@dataclass(frozen=True)
class StoreUrl:
    """A resolved store URL (``bzh:sql-portable``) and the sqlite file, if any, it names."""

    url: str

    @property
    def path(self) -> Path | None:  # ast-grep-ignore: bzh:property-delegates
        """``None`` for a non-sqlite backend (external by nature, so there is nothing to
        compare against) or an in-memory store, neither of which has a path to confine."""
        url = make_url(self.url)
        if url.get_backend_name() != "sqlite" or url.database in (None, ":memory:"):
            return None
        return Path(url.database)

    def confine(self, root: Path) -> None:
        """Refuse a sqlite path resolving outside ``root`` — a config carrying
        another runtime root's absolute store path would silently operate on that database
        once copied. A relative path resolves against the cwd, not ``root``, and is left alone."""
        path = self.path
        if path is None or not path.is_absolute():
            return
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ConfigError(
                f"{root}'s config names a db_url outside this directory: {path} — a copied or "
                "moved store directory would silently operate on the original database. Pass "
                "--allow-external-db to use it anyway."
            ) from exc


@dto
@dataclass(frozen=True)
class WorkSourceConfig:
    """One configured work source — a named, credentialed forge binding.
    ``token_env`` names the environment variable carrying the credential, never the
    secret itself; ``api_base``/``web_base`` override the provider's default origins,
    and ``web_base`` derives from ``api_base`` when omitted."""

    name: str
    provider: str
    repo: str
    token_env: str
    #: Opt into the forge-status label sweep — canonical instance only; two writers fight.
    annotate: bool = False
    api_base: str | None = None
    web_base: str | None = None


def _work_sources(raw_sources: object) -> tuple[WorkSourceConfig, ...]:
    """Validate and project ``[[work_source]]`` entries; each rejection names
    the offending entry rather than failing generically."""
    if not isinstance(raw_sources, list):
        return ()
    sources: list[WorkSourceConfig] = []
    seen_names: set[str] = set()
    seen_provider_repo: set[tuple[str, str]] = set()
    for entry in raw_sources:
        if not isinstance(entry, dict):
            raise ConfigError(f"[[work_source]] entry must be a table, got {entry!r}")
        missing = [key for key in _REQUIRED_WORK_SOURCE_KEYS if key not in entry]
        if missing:
            raise ConfigError(f"[[work_source]] entry is missing required key(s) {missing}: {entry!r}")
        name = str(entry["name"])
        provider = str(entry["provider"])
        repo = str(entry["repo"])
        token_env = str(entry["token_env"])
        if ":" in name:
            # A colon in a source name breaks the ingest-token grammar's first-colon split.
            raise ConfigError(f"[[work_source]] name {name!r} must not contain ':'")
        if name == RESERVED_HUB_SOURCE_NAME:
            # The built-in, always-seated source — a configured entry
            # of the same name would collide with it.
            raise ConfigError(f"[[work_source]] name {name!r} is reserved for the built-in hub source")
        if name in seen_names:
            raise ConfigError(f"duplicate [[work_source]] name {name!r}")
        seen_names.add(name)
        if "close" in entry:
            # Close intents have no per-source configuration key.
            raise ConfigError(f"[[work_source]] {name!r} has an unsupported close key — delete the key")
        provider_repo = (provider, repo)
        if provider_repo in seen_provider_repo:
            # Two names for one (provider, repo) would let the same item be ingested twice
            # under two identities — this is what holds pointer identity uniqueness up.
            raise ConfigError(f"duplicate [[work_source]] (provider, repo) {provider_repo!r} across two names")
        seen_provider_repo.add(provider_repo)
        if provider not in KNOWN_WORK_SOURCE_PROVIDERS:
            raise ConfigError(
                f"[[work_source]] {name!r} has unknown provider {provider!r} "
                f"(known: {sorted(KNOWN_WORK_SOURCE_PROVIDERS)})"
            )
        annotate = entry.get("annotate", False)
        if not isinstance(annotate, bool):
            # Validated rather than coerced, mirroring `follow_latest`: a source that opts
            # into writing to a shared forge deserves an explicit boolean, not a truthy guess.
            raise ConfigError(f"[[work_source]] {name!r} has annotate={annotate!r}, must be a boolean")
        api_base = str(entry["api_base"]) if entry.get("api_base") else None
        web_base = str(entry["web_base"]) if entry.get("web_base") else None
        sources.append(
            WorkSourceConfig(
                name=name,
                provider=provider,
                repo=repo,
                token_env=token_env,
                annotate=annotate,
                api_base=api_base,
                web_base=web_base,
            )
        )
    return tuple(sources)


@dto
@dataclass(frozen=True)
class OAuthProviderConfig:
    """One configured OAuth login provider. ``client_secret_env``
    names the environment variable carrying the secret, never the secret itself.
    ``api_base`` overrides the provider's default host — ``github`` type only, an
    ``oidc`` provider's ``issuer`` already naming its own."""

    name: str
    type: str
    display_name: str
    client_id: str
    client_secret_env: str
    issuer: str | None = None
    api_base: str | None = None


def _oauth_providers(raw_providers: object) -> tuple[OAuthProviderConfig, ...]:
    """Structurally validate and project ``[[auth.oauth.provider]]`` entries — required
    keys only; ``type``/``issuer`` semantic validation belongs to whichever consumer first
    uses a provider."""
    if not isinstance(raw_providers, list):
        return ()
    providers: list[OAuthProviderConfig] = []
    seen_names: set[str] = set()
    for entry in raw_providers:
        if not isinstance(entry, dict):
            raise ConfigError(f"[[auth.oauth.provider]] entry must be a table, got {entry!r}")
        missing = [key for key in _REQUIRED_OAUTH_PROVIDER_KEYS if key not in entry]
        if missing:
            raise ConfigError(f"[[auth.oauth.provider]] entry is missing required key(s) {missing}: {entry!r}")
        name = str(entry["name"])
        if name in seen_names:
            raise ConfigError(f"duplicate [[auth.oauth.provider]] name {name!r}")
        seen_names.add(name)
        issuer_raw = entry.get("issuer")
        api_base_raw = entry.get("api_base")
        providers.append(
            OAuthProviderConfig(
                name=name,
                type=str(entry["type"]),
                display_name=str(entry["display_name"]),
                client_id=str(entry["client_id"]),
                client_secret_env=str(entry["client_secret_env"]),
                issuer=str(issuer_raw) if issuer_raw else None,
                api_base=str(api_base_raw) if api_base_raw else None,
            )
        )
    return tuple(providers)


@dto
@dataclass(frozen=True)
class AuthConfig:
    """Resolved ``[auth]`` config — the human-auth rollout knob.

    ``mode`` defaults to :data:`AUTH_MODE_NONE`; ``superuser`` is a nullable email."""

    mode: str = AUTH_MODE_NONE
    superuser: str | None = None
    oauth_providers: tuple[OAuthProviderConfig, ...] = ()

    @classmethod
    def of(cls, raw_auth: object) -> AuthConfig:
        if not isinstance(raw_auth, dict):
            return cls()
        mode = str(raw_auth.get("mode", AUTH_MODE_NONE))
        if mode not in _KNOWN_AUTH_MODES:
            raise ConfigError(f"auth.mode must be one of {sorted(_KNOWN_AUTH_MODES)}, got {mode!r}")
        superuser_raw = raw_auth.get("superuser")
        oauth = raw_auth.get("oauth", {})
        raw_providers = oauth.get("provider", []) if isinstance(oauth, dict) else []
        return cls(
            mode=mode,
            superuser=str(superuser_raw) if superuser_raw else None,
            oauth_providers=_oauth_providers(raw_providers),
        )


@dto
@dataclass(frozen=True)
class TranscriptCapsConfig:
    """Resolved ``[transcripts]`` config — the ingest lane's three byte
    ceilings as OVERRIDES. ``None`` means "whatever the domain's default is", so this layer
    never restates a number the domain already owns (``bzh:one-prose-home``)."""

    record_max_bytes: int | None = None
    chunk_budget_max_bytes: int | None = None
    runner_daily_rate_max_bytes: int | None = None

    @classmethod
    def of(cls, raw_transcripts: object) -> TranscriptCapsConfig:
        if not isinstance(raw_transcripts, dict):
            return cls()
        return cls(
            record_max_bytes=_cap_bytes(raw_transcripts, "record_max_bytes"),
            chunk_budget_max_bytes=_cap_bytes(raw_transcripts, "chunk_budget_max_bytes"),
            runner_daily_rate_max_bytes=_cap_bytes(raw_transcripts, "runner_daily_rate_max_bytes"),
        )


def _cap_bytes(raw: Mapping[str, object], key: str) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"transcripts.{key} must be an integer number of bytes, got {value!r}")
    if value <= 0:
        # Zero would reject every record while reading as "unset" to an operator
        # skimming the file; there is no "disable the lane" meaning for a cap.
        raise ConfigError(f"transcripts.{key} must be positive, got {value!r}")
    return value


EGRESS_FORMATS = ("ndjson", "parquet")
#: The datasets an export can carry, in the order a pass writes them.
EGRESS_DATASETS = ("steps", "invocations", "events")
#: What leaves as a ``file_read`` event's subject.
EGRESS_FILE_PATHS = ("relative", "hashed", "absolute", "omit")
#: Which extractor versions' derivations the ``events`` dataset writes.
EGRESS_EXTRACTOR_VERSIONS = ("current", "all")


@dto
@dataclass(frozen=True)
class EgressConfig:
    """Resolved ``[egress]`` config — the fact-egress export's keys. It runs only when ``directory`` is set;
    every other key has a default that works unset."""

    directory: Path | None = None
    format: Literal["ndjson", "parquet"] = "ndjson"
    datasets: tuple[str, ...] = EGRESS_DATASETS
    sweep_seconds: int = 60
    #: How long a closed step or usage fact must have stood before it is exported; 0 exports at once.
    settle_seconds: int = 300
    batch_limit: int = 5000
    max_rows_per_file: int = 100000
    min_free_bytes: int = 1024**3
    #: The widest window a backfill may write, in seconds.
    backfill_max_window: int = 604800
    #: How a ``file_read`` event's path leaves: relative to the working directory, keyed-hashed, as stored, or omitted.
    file_paths: Literal["relative", "hashed", "absolute", "omit"] = "relative"
    #: The environment variable holding the HMAC key for hashed paths; the config never holds the secret itself.
    path_key_env: str = "BZ_EGRESS_PATH_KEY"
    #: Whether the ``events`` dataset writes only the hub's current extractor version's derivations, or every one.
    extractor_versions: Literal["current", "all"] = "current"

    @classmethod
    def of(cls, raw_egress: object) -> EgressConfig:
        if not isinstance(raw_egress, dict):
            return cls()
        defaults = cls()
        return cls(
            directory=_egress_directory(raw_egress),
            format=_egress_format(raw_egress, defaults.format),
            datasets=_egress_datasets(raw_egress, defaults.datasets),
            sweep_seconds=_egress_integer(raw_egress, "sweep_seconds", defaults.sweep_seconds, minimum=1),
            settle_seconds=_egress_integer(raw_egress, "settle_seconds", defaults.settle_seconds, minimum=0),
            batch_limit=_egress_integer(raw_egress, "batch_limit", defaults.batch_limit, minimum=1),
            max_rows_per_file=_egress_integer(raw_egress, "max_rows_per_file", defaults.max_rows_per_file, minimum=1),
            min_free_bytes=_egress_integer(raw_egress, "min_free_bytes", defaults.min_free_bytes, minimum=0),
            backfill_max_window=_egress_integer(
                raw_egress, "backfill_max_window", defaults.backfill_max_window, minimum=1
            ),
            file_paths=_egress_file_paths(raw_egress, defaults.file_paths),
            path_key_env=_egress_path_key_env(raw_egress, defaults.path_key_env),
            extractor_versions=_egress_extractor_versions(raw_egress, defaults.extractor_versions),
        )

    def to_toml(self) -> list[str]:
        """The ``[egress]`` block. ``directory`` is the switch and has no default; every other key is rendered
        commented out at its default, live once overridden."""
        defaults = EgressConfig()
        lines = [
            "\n# Fact egress: write closed steps, usage and transcript events as immutable files an analytics\n"
            "# tool can load. Off until `directory` is set. format is ndjson or parquet (parquet needs the\n"
            "# blizzard[egress] extra); datasets draws from steps, invocations and events. Seconds, except\n"
            "# batch_limit and max_rows_per_file (rows) and min_free_bytes. file_paths decides what a file\n"
            "# read's path leaves as (relative, hashed, absolute or omit); path_key_env names the\n"
            "# environment variable holding the key for hashed paths. extractor_versions is current or\n"
            "# all: which extractor versions' events are written. Uncomment to override.\n",
            "[egress]\n",
            '# directory = "/var/lib/blizzard/egress"\n'
            if self.directory is None
            else f"directory = {json.dumps(str(self.directory))}\n",
        ]
        for key in _EGRESS_KEYS:
            value = getattr(self, key)
            literal = json.dumps(list(value)) if isinstance(value, tuple) else json.dumps(value)
            lines.append(f"# {key} = {literal}\n" if value == getattr(defaults, key) else f"{key} = {literal}\n")
        return lines


def _egress_directory(raw: Mapping[str, object]) -> Path | None:
    value = raw.get("directory")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"egress.directory must be a non-empty path, got {value!r}")
    return Path(value).expanduser()


def _egress_format(raw: Mapping[str, object], default: Literal["ndjson", "parquet"]) -> Literal["ndjson", "parquet"]:
    value = raw.get("format", default)
    if value == "ndjson":
        return "ndjson"
    if value == "parquet":
        return "parquet"
    raise ConfigError(f"egress.format must be one of {', '.join(EGRESS_FORMATS)}, got {value!r}")


def _egress_file_paths(
    raw: Mapping[str, object], default: Literal["relative", "hashed", "absolute", "omit"]
) -> Literal["relative", "hashed", "absolute", "omit"]:
    value = raw.get("file_paths", default)
    if value == "relative":
        return "relative"
    if value == "hashed":
        return "hashed"
    if value == "absolute":
        return "absolute"
    if value == "omit":
        return "omit"
    raise ConfigError(f"egress.file_paths must be one of {', '.join(EGRESS_FILE_PATHS)}, got {value!r}")


def _egress_extractor_versions(
    raw: Mapping[str, object], default: Literal["current", "all"]
) -> Literal["current", "all"]:
    value = raw.get("extractor_versions", default)
    if value == "current":
        return "current"
    if value == "all":
        return "all"
    raise ConfigError(f"egress.extractor_versions must be one of {', '.join(EGRESS_EXTRACTOR_VERSIONS)}, got {value!r}")


def _egress_path_key_env(raw: Mapping[str, object], default: str) -> str:
    value = raw.get("path_key_env", default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"egress.path_key_env must be a non-empty environment variable name, got {value!r}")
    return value


def _egress_datasets(raw: Mapping[str, object], default: tuple[str, ...]) -> tuple[str, ...]:
    value = raw.get("datasets", list(default))
    if not isinstance(value, list) or not value or any(item not in EGRESS_DATASETS for item in value):
        raise ConfigError(f"egress.datasets must be a non-empty list drawn from {list(EGRESS_DATASETS)}, got {value!r}")
    # Pass order, not file order: steps first, once each.
    return tuple(name for name in EGRESS_DATASETS if name in value)


def _egress_integer(raw: Mapping[str, object], key: str, default: int, *, minimum: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"egress.{key} must be an integer, got {value!r}")
    if value < minimum:
        bound = "non-negative" if minimum == 0 else "positive"
        raise ConfigError(f"egress.{key} must be {bound}, got {value!r}")
    return value


_EGRESS_KEYS = (
    "format",
    "datasets",
    "sweep_seconds",
    "settle_seconds",
    "batch_limit",
    "max_rows_per_file",
    "min_free_bytes",
    "backfill_max_window",
    "file_paths",
    "path_key_env",
    "extractor_versions",
)


@dto
@dataclass(frozen=True)
class HubConfig:
    """Resolved hub runtime configuration."""

    root: Path
    db_url: str
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    work_sources: tuple[WorkSourceConfig, ...] = ()
    runner_auth_mode: str = RUNNER_AUTH_WARN
    route_token_mode: str = ROUTE_TOKEN_WARN
    produces_mode: str = PRODUCES_WARN
    #: Fleet-wide default for re-pinning a chunk to its graph name's newest mint.
    follow_latest: bool = False
    #: Forge-status sweep cadence in seconds; consulted only when a source annotates.
    annotation_interval_seconds: int = 120
    #: Instance-level forge-write posture for closing — false declines every
    #: configured source's closer (never the built-in hub source's, which writes no forge)
    #: so a non-canonical hub can't close real items; true (the default) is unconditional, so a
    #: non-canonical hub must opt out explicitly against live forge writes.
    close_forge_writes_enabled: bool = True
    auth: AuthConfig = field(default_factory=AuthConfig)
    #: Transcript ingest cap overrides; every field None = the domain defaults.
    transcripts: TranscriptCapsConfig = field(default_factory=TranscriptCapsConfig)
    #: Fleet-trace sweep knobs; every field at its default when ``[tracing]`` is absent.
    tracing: TracingConfig = field(default_factory=TracingConfig)
    #: Fact-egress export; no sweep runs unless ``[egress] directory`` is set.
    egress: EgressConfig = field(default_factory=EgressConfig)
    #: Reverse-proxy trust set — addresses or CIDRs whose forwarded headers are honored.
    trusted_proxies: tuple[str, ...] = ()
    #: Absolute ``http(s)`` board origin; ``None`` omits every board link.
    public_url: str | None = None

    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_FILENAME

    @property
    def data_dir(self) -> Path:
        return self.root / DATA_DIRNAME

    @classmethod
    def scaffold(cls, root: Path) -> HubConfig:
        """The default config for a fresh runtime root (used by ``init``)."""
        env = Env()
        return cls(
            root=root,
            db_url=env.text(ENV_DB_URL, default_db_url(root)),
            host=env.text(ENV_HOST, DEFAULT_HOST),
            port=env.port(DEFAULT_PORT),
        )

    def _transcript_cap_lines(self) -> list[str]:
        """The ``[transcripts]`` block. Every cap is rendered — commented out at its default,
        live once overridden — so the file always shows an operator what the ceilings ARE
        without them having to find the constants. Values come from the domain's own
        defaults, never restated here."""
        defaults = TranscriptCaps()
        caps = (
            ("record_max_bytes", self.transcripts.record_max_bytes, defaults.record_max_bytes),
            ("chunk_budget_max_bytes", self.transcripts.chunk_budget_max_bytes, defaults.chunk_budget_max_bytes),
            (
                "runner_daily_rate_max_bytes",
                self.transcripts.runner_daily_rate_max_bytes,
                defaults.runner_daily_rate_max_bytes,
            ),
        )
        lines = [
            "\n# Transcript ingest ceilings, in bytes. Each is shown at its\n"
            "# default; uncomment to override. Widen these for a backfill window — a\n"
            "# `blizzard runner transcript reship` spends the per-chunk budget a SECOND time —\n"
            "# then restore them. `record_max_bytes` must stay at or ABOVE the runner's own\n"
            "# per-record cap: a record over this one loses its turns whole, where the runner's\n"
            "# cap merely shrinks it.\n",
            "[transcripts]\n",
        ]
        lines += [
            f"{key} = {value}\n" if value is not None else f"# {key} = {default}\n" for key, value, default in caps
        ]
        return lines

    def to_toml(self) -> str:
        lines = ["# blizzard-hub runtime configuration (blizzard hub init)\n"]
        if self.db_url != default_db_url(self.root):
            # The default is omitted rather than serialized absolute: `load`
            # re-derives it, so a copied runtime root stays self-contained.
            lines.append(f'db_url = "{self.db_url}"\n')
        lines += [
            f'host = "{self.host}"\n',
            f"port = {self.port}\n",
            f'runner_auth_mode = "{self.runner_auth_mode}"\n',
            f'route_token_mode = "{self.route_token_mode}"\n',
            f'produces_mode = "{self.produces_mode}"\n',
            "\n# Follow-latest: when true, a chunk re-pins to the newest enabled\n"
            "# mint of its own graph's NAME at its next transition, so a workflow edit reaches\n"
            "# in-flight work without migrating each chunk by hand. A graph's own follow_latest\n"
            "# overrides this; false (the default) keeps every chunk on the mint it started on.\n",
            f"follow_latest = {str(self.follow_latest).lower()}\n",
            "\n# Forge-status sweep cadence, in seconds. Only consulted when at\n"
            "# least one [[work_source]] below sets annotate = true; a hub with none starts\n"
            "# no sweep loop regardless of this value.\n",
            f"annotation_interval_seconds = {self.annotation_interval_seconds}\n",
            "\n# Forge-write posture for closing delivered work items. true (the\n"
            "# default) closes through every configured [[work_source]]; set false on a\n"
            "# non-canonical hub — dev, staging, or a restored snapshot — so it never writes to\n"
            "# a live forge repo. The built-in hub source is unaffected: it writes no forge.\n",
            f"close_forge_writes_enabled = {str(self.close_forge_writes_enabled).lower()}\n",
            "\n# Reverse-proxy trust set: proxy IPs/CIDRs whose forwarded\n"
            "# X-Forwarded-Proto/-For headers are honored (cookie Secure flag, login-throttle\n"
            "# key, auth-fact actor IP). Empty = ignore those headers from every peer.\n",
            f"trusted_proxies = [{', '.join(f'"{p}"' for p in self.trusted_proxies)}]\n",
            "\n# The absolute http(s) origin this hub's board is publicly reached at. When\n"
            "# set, a delivered PR's body links its chunk's board page; unset, every board\n"
            "# link is omitted. Never the bind address — a hosted hub is reached through a proxy.\n",
            f'public_url = "{self.public_url}"\n'
            if self.public_url
            else '# public_url = "https://blizzard.example.com"\n',
            *self._transcript_cap_lines(),
            *self.tracing.to_toml(unit="closed steps"),
            *self.egress.to_toml(),
        ]
        if not self.work_sources:
            lines.append(_WORK_SOURCE_EXAMPLE_COMMENT)
        for source in self.work_sources:
            lines.append("\n[[work_source]]\n")
            lines.append(f'name = "{source.name}"\n')
            lines.append(f'provider = "{source.provider}"\n')
            lines.append(f'repo = "{source.repo}"\n')
            lines.append(f'token_env = "{source.token_env}"\n')
            lines.append(f"annotate = {str(source.annotate).lower()}\n")
            if source.api_base is not None:
                lines.append(f'api_base = "{source.api_base}"\n')
            if source.web_base is not None:
                lines.append(f'web_base = "{source.web_base}"\n')
        lines.append("\n[auth]\n")
        lines.append(f'mode = "{self.auth.mode}"\n')
        if self.auth.superuser is not None:
            lines.append(f'superuser = "{self.auth.superuser}"\n')
        if not self.auth.oauth_providers:
            lines.append(_AUTH_OAUTH_PROVIDER_EXAMPLE_COMMENT)
        for provider in self.auth.oauth_providers:
            lines.append("\n[[auth.oauth.provider]]\n")
            lines.append(f'name = "{provider.name}"\n')
            lines.append(f'type = "{provider.type}"\n')
            lines.append(f'display_name = "{provider.display_name}"\n')
            lines.append(f'client_id = "{provider.client_id}"\n')
            lines.append(f'client_secret_env = "{provider.client_secret_env}"\n')
            if provider.issuer is not None:
                lines.append(f'issuer = "{provider.issuer}"\n')
            if provider.api_base is not None:
                lines.append(f'api_base = "{provider.api_base}"\n')
        return "".join(lines)

    @classmethod
    def load(
        cls,
        root: Path,
        *,
        host: str | None = None,
        port: int | None = None,
        allow_external_db: bool = False,
    ) -> HubConfig:
        """Read a runtime root's config file; overlay CLI host/port when given.

        ``db_url``/``host``/``port`` each resolve **CLI flag > environment > toml >
        default** (no CLI flag exists for ``db_url``). The resolved ``db_url`` is guarded
        against naming a sqlite path outside ``root``; ``allow_external_db`` opts out."""
        root = root.resolve()
        path = root / CONFIG_FILENAME
        if not path.exists():
            raise ConfigError(f"{root} is not an initialized hub runtime (run `blizzard hub init {root}`)")
        raw = tomllib.loads(path.read_text())
        runner_auth_mode = str(raw.get("runner_auth_mode", RUNNER_AUTH_WARN))
        if runner_auth_mode not in _KNOWN_RUNNER_AUTH_MODES:
            raise ConfigError(
                f"runner_auth_mode must be one of {sorted(_KNOWN_RUNNER_AUTH_MODES)}, got {runner_auth_mode!r}"
            )
        route_token_mode = str(raw.get("route_token_mode", ROUTE_TOKEN_WARN))
        if route_token_mode not in _KNOWN_ROUTE_TOKEN_MODES:
            raise ConfigError(
                f"route_token_mode must be one of {sorted(_KNOWN_ROUTE_TOKEN_MODES)}, got {route_token_mode!r}"
            )
        produces_mode = str(raw.get("produces_mode", PRODUCES_WARN))
        if produces_mode not in _KNOWN_PRODUCES_MODES:
            raise ConfigError(f"produces_mode must be one of {sorted(_KNOWN_PRODUCES_MODES)}, got {produces_mode!r}")
        follow_latest = raw.get("follow_latest", False)
        if not isinstance(follow_latest, bool):
            # Validated rather than coerced: `follow_latest = "true"` is a plausible typo,
            # and truthy-coercing it would silently arm a fleet-wide migration policy.
            raise ConfigError(f"follow_latest must be a boolean, got {follow_latest!r}")
        close_forge_writes_enabled = raw.get("close_forge_writes_enabled", True)
        if not isinstance(close_forge_writes_enabled, bool):
            raise ConfigError(f"close_forge_writes_enabled must be a boolean, got {close_forge_writes_enabled!r}")
        if RENAMED_WORK_SOURCE_KEY in raw:
            raise ConfigError(
                f"[[{RENAMED_WORK_SOURCE_KEY}]] is now [[work_source]] — rename the block(s) in "
                f"{path}. Leaving the old key would configure zero external work sources: "
                "every board label for a pointer outside the built-in `hub` source would render null."
            )
        toml_port = int(raw.get("port", DEFAULT_PORT))
        env = Env()
        db_url = env.text(ENV_DB_URL) or str(raw.get("db_url") or default_db_url(root))
        if not allow_external_db:
            StoreUrl(db_url).confine(root)
        return cls(
            root=root,
            db_url=db_url,
            host=host or env.text(ENV_HOST) or str(raw.get("host", DEFAULT_HOST)),
            port=port if port is not None else env.port(toml_port),
            work_sources=_work_sources(raw.get("work_source", [])),
            runner_auth_mode=runner_auth_mode,
            route_token_mode=route_token_mode,
            produces_mode=produces_mode,
            follow_latest=follow_latest,
            annotation_interval_seconds=int(raw.get("annotation_interval_seconds", 120)),
            close_forge_writes_enabled=close_forge_writes_enabled,
            auth=AuthConfig.of(raw.get("auth", {})),
            transcripts=TranscriptCapsConfig.of(raw.get("transcripts", {})),
            tracing=TracingConfig.of(raw.get("tracing", {}), ConfigError),
            egress=EgressConfig.of(raw.get("egress", {})),
            trusted_proxies=TrustedProxies.entries(raw.get("trusted_proxies"), ConfigError),
            public_url=parse_public_url(raw.get("public_url")),
        )


def parse_public_url(value: object) -> str | None:
    """``public_url`` validated — absent stays ``None``; anything but an absolute
    ``http(s)`` URL is a :class:`ConfigError`."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigError(f"public_url must be a string, got {value!r}")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ConfigError(f"public_url must be an absolute http(s) URL, got {value!r}")
    return value.rstrip("/")


def default_db_url(root: Path) -> str:
    return f"sqlite:///{(root / DATA_DIRNAME / 'hub.db').resolve()}"
