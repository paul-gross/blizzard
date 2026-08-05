"""Hub runtime configuration — resolved from a runtime directory.

The store URL is the single portability knob (``bzh:sql-portable``): the sqlite
default lives under the data dir, and postgres is the same config with a different
URL. The bind port falls back to ``BZ_HUB_PORT``. There is no stdlib TOML writer, so
:meth:`HubConfig.to_toml` hand-rolls the emit."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.engine import make_url

from blizzard.foundation.forwarded import TrustedProxies

CONFIG_FILENAME = "blizzard-hub.toml"
DATA_DIRNAME = "data"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8421

ENV_HOST = "BZ_HUB_HOST"
ENV_PORT = "BZ_HUB_PORT"
# Varies the store URL by environment rather than baking one per image; honored
# identically by every verb, which all resolve through `load` (`bzh:sql-portable`).
ENV_DB_URL = "BZ_HUB_DB_URL"

# The runner-identity rollout brake (issue #86a) — `warn` logs a missing/invalid bearer
# token and proceeds; `enforce` rejects. Defaults to `warn` so tokens can enroll first.
RUNNER_AUTH_WARN = "warn"
RUNNER_AUTH_ENFORCE = "enforce"
_KNOWN_RUNNER_AUTH_MODES = {RUNNER_AUTH_WARN, RUNNER_AUTH_ENFORCE}

# The route-capability-token rollout brake (issue #84b), separate from `runner_auth_mode`
# so the two enforce independently — `warn` proceeds; `enforce` rejects before the fence.
ROUTE_TOKEN_WARN = "warn"
ROUTE_TOKEN_ENFORCE = "enforce"
_KNOWN_ROUTE_TOKEN_MODES = {ROUTE_TOKEN_WARN, ROUTE_TOKEN_ENFORCE}

# The produces-artifact rollout brake (issue #113), separate from the two above — `warn`
# logs a `produces:` name with no attachment and proceeds; `enforce` rejects it.
PRODUCES_WARN = "warn"
PRODUCES_ENFORCE = "enforce"
_KNOWN_PRODUCES_MODES = {PRODUCES_WARN, PRODUCES_ENFORCE}

# The only work-source provider grammar a source may declare; an unknown provider fails
# at config load, not at first use.
_KNOWN_WORK_SOURCE_PROVIDERS = {"github"}
_REQUIRED_WORK_SOURCE_KEYS = ("name", "provider", "repo", "token_env")

# `[[work_source]]`'s pre-rename name (issue #55) — deliberately *not* aliased; pinned by
# `test_config.py::test_a_leftover_pm_source_block_fails_the_load_naming_the_new_key`.
RENAMED_WORK_SOURCE_KEY = "pm_source"

# The human-auth rollout knob (issue #91) — `none` (the default) resolves every request
# to an implicit identity with no store read; `oauth` activates the session seam.
AUTH_MODE_NONE = "none"
AUTH_MODE_OAUTH = "oauth"
_KNOWN_AUTH_MODES = {AUTH_MODE_NONE, AUTH_MODE_OAUTH}

# `[[auth.oauth.provider]]` required keys — structural presence only (issue #91);
# secret resolution and `type`/`issuer` validation happen where a provider is consumed.
_REQUIRED_OAUTH_PROVIDER_KEYS = ("name", "type", "display_name", "client_id", "client_secret_env")

# A fresh scaffold has no configured source, and without one `work-items` 503s — so
# `to_toml()` emits this as a comment rather than leaving the block undiscoverable.
_WORK_SOURCE_EXAMPLE_COMMENT = """
# Uncomment and edit to configure a work source — without at least one
# [[work_source]], `work-items` 503s and board pointer labels render null.
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
# close = false                # opt into the delivery closure sweep; only the canonical
#                              # instance for a repo should ever set this to true — two
#                              # writers against the same forge repo will fight
# api_base = "https://ghe.example.internal/api/v3"  # optional: override the API origin (e.g. GHE)
# web_base = "https://ghe.example.internal"          # optional: override the web origin; derives from api_base
"""

# Mirrors `_WORK_SOURCE_EXAMPLE_COMMENT` — emitted when `[auth]` carries no configured
# login provider, so the block stays discoverable under `mode = "none"` (issue #91).
_AUTH_OAUTH_PROVIDER_EXAMPLE_COMMENT = """
# Uncomment and edit to declare an OAuth login provider — consumed once `mode =
# "oauth"` and a login mechanism exist (issue #92); parsed-and-carried here so the
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
    #: Opt into the forge-status label sweep (issue #179) — canonical instance only; two writers fight.
    annotate: bool = False
    #: Opt into the delivery closure sweep (issue #216) — canonical instance only; two writers fight.
    close: bool = False
    api_base: str | None = None
    web_base: str | None = None


@dataclass(frozen=True)
class OAuthProviderConfig:
    """One configured OAuth login provider (issues #91, #92). ``client_secret_env``
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


@dataclass(frozen=True)
class AuthConfig:
    """Resolved ``[auth]`` config (issue #91) — the human-auth rollout knob.

    ``mode`` defaults to :data:`AUTH_MODE_NONE`; ``superuser`` is a nullable email."""

    mode: str = AUTH_MODE_NONE
    superuser: str | None = None
    oauth_providers: tuple[OAuthProviderConfig, ...] = ()


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
    #: Fleet-wide default for re-pinning a chunk to its graph name's newest mint (issue #164).
    follow_latest: bool = False
    #: Forge-status sweep cadence in seconds (issue #179); consulted only when a source annotates.
    annotation_interval_seconds: int = 120
    auth: AuthConfig = field(default_factory=AuthConfig)
    #: Reverse-proxy trust set (issue #130) — addresses or CIDRs whose forwarded headers are honored.
    trusted_proxies: tuple[str, ...] = ()

    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_FILENAME

    @property
    def data_dir(self) -> Path:
        return self.root / DATA_DIRNAME

    @staticmethod
    def default_db_url(root: Path) -> str:
        return f"sqlite:///{(root / DATA_DIRNAME / 'hub.db').resolve()}"

    @classmethod
    def scaffold(cls, root: Path) -> HubConfig:
        """The default config for a fresh runtime root (used by ``init``)."""
        return cls(
            root=root,
            db_url=os.environ.get(ENV_DB_URL, cls.default_db_url(root)),
            host=os.environ.get(ENV_HOST, DEFAULT_HOST),
            port=_resolve_port_env(os.environ.get(ENV_PORT), DEFAULT_PORT),
        )

    def to_toml(self) -> str:
        lines = ["# blizzard-hub runtime configuration (blizzard hub init)\n"]
        if self.db_url != self.default_db_url(self.root):
            # The default is omitted rather than serialized absolute (issue #234): `load`
            # re-derives it, so a copied runtime root stays self-contained.
            lines.append(f'db_url = "{self.db_url}"\n')
        lines += [
            f'host = "{self.host}"\n',
            f"port = {self.port}\n",
            f'runner_auth_mode = "{self.runner_auth_mode}"\n',
            f'route_token_mode = "{self.route_token_mode}"\n',
            f'produces_mode = "{self.produces_mode}"\n',
            "\n# Follow-latest (issue #164): when true, a chunk re-pins to the newest enabled\n"
            "# mint of its own graph's NAME at its next transition, so a workflow edit reaches\n"
            "# in-flight work without migrating each chunk by hand. A graph's own follow_latest\n"
            "# overrides this; false (the default) keeps every chunk on the mint it started on.\n",
            f"follow_latest = {str(self.follow_latest).lower()}\n",
            "\n# Forge-status sweep cadence (issue #179), in seconds. Only consulted when at\n"
            "# least one [[work_source]] below sets annotate = true; a hub with none starts\n"
            "# no sweep loop regardless of this value.\n",
            f"annotation_interval_seconds = {self.annotation_interval_seconds}\n",
            "\n# Reverse-proxy trust set (issue #130): proxy IPs/CIDRs whose forwarded\n"
            "# X-Forwarded-Proto/-For headers are honored (cookie Secure flag, login-throttle\n"
            "# key, auth-fact actor IP). Empty = ignore those headers from every peer.\n",
            f"trusted_proxies = [{', '.join(f'"{p}"' for p in self.trusted_proxies)}]\n",
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
            lines.append(f"close = {str(source.close).lower()}\n")
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
        if RENAMED_WORK_SOURCE_KEY in raw:
            raise ConfigError(
                f"[[{RENAMED_WORK_SOURCE_KEY}]] is now [[work_source]] — rename the block(s) in "
                f"{path}. Leaving the old key would configure zero work sources: "
                "`work-items` would 503 and every board label would render null."
            )
        toml_port = int(raw.get("port", DEFAULT_PORT))
        db_url = os.environ.get(ENV_DB_URL) or str(raw.get("db_url") or cls.default_db_url(root))
        _guard_db_url_within_root(root, db_url, allow_external_db=allow_external_db)
        return cls(
            root=root,
            db_url=db_url,
            host=host or os.environ.get(ENV_HOST) or str(raw.get("host", DEFAULT_HOST)),
            port=port if port is not None else _resolve_port_env(os.environ.get(ENV_PORT), toml_port),
            work_sources=_parse_work_sources(raw.get("work_source", [])),
            runner_auth_mode=runner_auth_mode,
            route_token_mode=route_token_mode,
            produces_mode=produces_mode,
            follow_latest=follow_latest,
            annotation_interval_seconds=int(raw.get("annotation_interval_seconds", 120)),
            auth=_parse_auth(raw.get("auth", {})),
            trusted_proxies=_parse_trusted_proxies(raw.get("trusted_proxies", ())),
        )


def _sqlite_db_path(db_url: str) -> Path | None:
    """The filesystem path a ``sqlite:///`` URL names, or ``None`` when ``db_url`` is not
    sqlite (postgres, etc — inherently external, so the ``--dir`` guard below has nothing
    to compare against and passes it untouched; ``bzh:sql-portable``) or names an in-memory
    store (``sqlite://``/``sqlite:///:memory:``, which has no path to guard)."""
    url = make_url(db_url)
    if url.get_backend_name() != "sqlite" or url.database in (None, ":memory:"):
        return None
    return Path(url.database)


def _guard_db_url_within_root(root: Path, db_url: str, *, allow_external_db: bool) -> None:
    """Refuse a ``db_url`` whose sqlite path resolves outside ``root`` (issue #234).

    A config carrying an absolute store path from another runtime root would otherwise
    silently operate on that original database once copied. A relative sqlite path is
    left alone: it resolves against the process's cwd, not against ``root``."""
    if allow_external_db:
        return
    path = _sqlite_db_path(db_url)
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


def _resolve_port_env(raw: str | None, fallback: int) -> int:
    """Parse ``BZ_HUB_PORT`` when set, naming the variable on a malformed value —
    shared by :meth:`HubConfig.scaffold` and :meth:`HubConfig.load` so a container's
    very first boot (``init``, which scaffolds) and every later boot (``load``) fail
    identically instead of ``scaffold`` raising a raw ``ValueError``."""
    if raw is None:
        return fallback
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{ENV_PORT} must be an integer, got {raw!r}") from exc


def _parse_work_sources(raw_sources: object) -> tuple[WorkSourceConfig, ...]:
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
        if name in seen_names:
            raise ConfigError(f"duplicate [[work_source]] name {name!r}")
        seen_names.add(name)
        provider_repo = (provider, repo)
        if provider_repo in seen_provider_repo:
            # Two names for one (provider, repo) would let the same item be ingested twice
            # under two identities — this is what holds pointer identity uniqueness up.
            raise ConfigError(f"duplicate [[work_source]] (provider, repo) {provider_repo!r} across two names")
        seen_provider_repo.add(provider_repo)
        if provider not in _KNOWN_WORK_SOURCE_PROVIDERS:
            raise ConfigError(
                f"[[work_source]] {name!r} has unknown provider {provider!r} "
                f"(known: {sorted(_KNOWN_WORK_SOURCE_PROVIDERS)})"
            )
        annotate = entry.get("annotate", False)
        if not isinstance(annotate, bool):
            # Validated rather than coerced, mirroring `follow_latest`: a source that opts
            # into writing to a shared forge deserves an explicit boolean, not a truthy guess.
            raise ConfigError(f"[[work_source]] {name!r} has annotate={annotate!r}, must be a boolean")
        close = entry.get("close", False)
        if not isinstance(close, bool):
            # Mirrors `annotate`'s own validated-not-coerced rationale.
            raise ConfigError(f"[[work_source]] {name!r} has close={close!r}, must be a boolean")
        api_base = str(entry["api_base"]) if entry.get("api_base") else None
        web_base = str(entry["web_base"]) if entry.get("web_base") else None
        sources.append(
            WorkSourceConfig(
                name=name,
                provider=provider,
                repo=repo,
                token_env=token_env,
                annotate=annotate,
                close=close,
                api_base=api_base,
                web_base=web_base,
            )
        )
    return tuple(sources)


def _parse_trusted_proxies(raw: object) -> tuple[str, ...]:
    """``trusted_proxies`` (issue #130) — validate each entry parses as an IP or CIDR
    (via :meth:`TrustedProxies.parse`) so a malformed proxy fails at config load, then
    carry the raw strings (they round-trip to toml verbatim; parsing into networks is the
    composition root's job)."""
    if not isinstance(raw, (list, tuple)):
        return ()
    entries = tuple(str(entry).strip() for entry in raw)
    try:
        TrustedProxies.parse(entries)
    except ValueError as exc:
        raise ConfigError(f"trusted_proxies entry is not a valid IP or CIDR: {exc}") from exc
    return entries


def _parse_auth(raw_auth: object) -> AuthConfig:
    """Parse ``[auth]`` (issue #91) — ``mode``/``superuser`` are validated here;
    ``[[auth.oauth.provider]]`` entries are structurally parsed-and-carried, not
    semantically validated (that is #92's job, once a provider is actually consumed)."""
    if not isinstance(raw_auth, dict):
        return AuthConfig()
    mode = str(raw_auth.get("mode", AUTH_MODE_NONE))
    if mode not in _KNOWN_AUTH_MODES:
        raise ConfigError(f"auth.mode must be one of {sorted(_KNOWN_AUTH_MODES)}, got {mode!r}")
    superuser_raw = raw_auth.get("superuser")
    superuser = str(superuser_raw) if superuser_raw else None
    oauth = raw_auth.get("oauth", {})
    raw_providers = oauth.get("provider", []) if isinstance(oauth, dict) else []
    return AuthConfig(mode=mode, superuser=superuser, oauth_providers=_parse_oauth_providers(raw_providers))


def _parse_oauth_providers(raw_providers: object) -> tuple[OAuthProviderConfig, ...]:
    """Structurally validate and project ``[[auth.oauth.provider]]`` entries — required
    keys only; ``type``/``issuer`` semantic validation is #92's concern once a provider
    is actually consumed."""
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
