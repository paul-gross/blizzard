"""Hub runtime configuration — resolved from a runtime directory.

``blizzard hub init <dir>`` scaffolds a config file and a data directory under a
runtime root; the daemon and the offline ``migrate`` verb read it back. The store
URL is the single portability knob (``bzh:sql-portable``): the sqlite default
lives under the data dir, and postgres is the same config with a different URL.
The bind port falls back to the winter service band's ``BZ_HUB_PORT`` (band +2).

``[[pm_source]]`` is the zero-or-more configured PM work sources: each a
named, credentialed forge binding the composition root (``hub/pm/internal/factory.py``)
turns into one ``httpx.Client`` + adapter instance. ``tomllib`` parses the array of
tables for free; there is no stdlib TOML writer, so :meth:`HubConfig.to_toml` hand-rolls
the emit in the same string-concat style as the rest of this file.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_FILENAME = "blizzard-hub.toml"
DATA_DIRNAME = "data"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8421

ENV_HOST = "BZ_HUB_HOST"
ENV_PORT = "BZ_HUB_PORT"

# The runner-authentication rollout brake (issue #86a) — `warn` logs a missing/invalid/
# mismatched bearer token and lets the request proceed; `enforce` rejects it. Ship
# defaulting to `warn`; the dogfooding fleet flips to `enforce` once its runtime env
# files carry enrolled tokens (an operator step, out of scope here). Named
# `runner_auth_mode` for the *runner-identity* brake specifically — #84 adds a
# separate `route_token_mode` for the per-acquisition route capability token, so the
# two enforce independently.
RUNNER_AUTH_WARN = "warn"
RUNNER_AUTH_ENFORCE = "enforce"
_KNOWN_RUNNER_AUTH_MODES = {RUNNER_AUTH_WARN, RUNNER_AUTH_ENFORCE}

# The route-capability-token rollout brake (issue #84b) — a **separate** flag from
# `runner_auth_mode` above, so route-token authorization enforces independently of
# runner identity (a fleet can flip one on before the other). `warn` logs a
# missing/mismatched route token and lets the chunk-scoped write/fact proceed;
# `enforce` rejects it as a semantic failure, before the epoch fence. Ship `warn`; the
# operator flips to `enforce` once outbound buffers carrying pre-upgrade,
# token-less facts have drained (no separate grace period is needed — `warn` covers
# that window).
ROUTE_TOKEN_WARN = "warn"
ROUTE_TOKEN_ENFORCE = "enforce"
_KNOWN_ROUTE_TOKEN_MODES = {ROUTE_TOKEN_WARN, ROUTE_TOKEN_ENFORCE}

# The produces-artifact rollout brake (issue #113 phase 5) — a **separate** flag from
# ``route_token_mode``/``runner_auth_mode`` above, gating the hub-side backstop on top of
# the runner's own nudge-once (issue #113 phase 4): completion assembly already prefers an
# explicit ``blizzard runner attach`` over the judgement-assessment fallback, so a
# `produces:` name still lacking an explicit attachment at submission time is a signal the
# nudge did not resolve. `warn` logs the missing-explicit-artifact names and lets the
# completion proceed unchanged (assessment fallback still lands, exactly as before this
# phase); `enforce` rejects the completion as a semantic failure, before the transition is
# recorded. Ship `warn`; the operator flips to `enforce` once packaged prompts (phase 6)
# and the runner nudge (phase 4, already landed) have had time to drive worker behavior.
PRODUCES_WARN = "warn"
PRODUCES_ENFORCE = "enforce"
_KNOWN_PRODUCES_MODES = {PRODUCES_WARN, PRODUCES_ENFORCE}

# The only PM provider grammar a source may declare; an unknown provider fails
# at config load, not at first use.
_KNOWN_PM_PROVIDERS = {"github"}
_REQUIRED_PM_SOURCE_KEYS = ("name", "provider", "repo", "token_env")

# The human-auth rollout knob (issue #91) — `none` (the default, and it stays the
# shipped default until epic #89 completes) resolves every request to the implicit
# `operator`/`superuser` identity with no store read; `oauth` activates the session/
# permission seam. Validated exactly like `runner_auth_mode`.
AUTH_MODE_NONE = "none"
AUTH_MODE_OAUTH = "oauth"
_KNOWN_AUTH_MODES = {AUTH_MODE_NONE, AUTH_MODE_OAUTH}

# `[[auth.oauth.provider]]` required keys — parsed-and-carried in #91 (this issue) so
# the config schema is stable for #92, which is the phase that actually *consumes* a
# provider entry (resolving its secret, validating `type`/`issuer`). #91 only checks
# structural presence.
_REQUIRED_OAUTH_PROVIDER_KEYS = ("name", "type", "display_name", "client_id", "client_secret_env")

# A fresh scaffold has no configured source, and without one `pm-items` 503s and board
# pointer labels go null (you cannot render `{source}#{ref}` without a source name) — so
# `to_toml()` emits this as a comment rather than leaving the block undiscoverable.
_PM_SOURCE_EXAMPLE_COMMENT = """
# Uncomment and edit to configure a PM work source — without at least one
# [[pm_source]], `pm-items` 503s and board pointer labels render null.
#
# [[pm_source]]
# name = "blizzard"          # names this source; ingest tokens and board labels key on it
# provider = "github"        # the only adapter grammar that exists today
# repo = "owner/name"        # the "owner/repo" this source is pinned to
# token_env = "BZ_PM_TOKEN"  # names an env var — the secret itself lives in this
#                             # runtime's env file (e.g. /etc/blizzard/hub.env), never here
# api_base = "https://ghe.example.internal/api/v3"  # optional: override the API origin (e.g. GHE)
# web_base = "https://ghe.example.internal"          # optional: override the web origin; derives from api_base
"""

# Mirrors `_PM_SOURCE_EXAMPLE_COMMENT` — emitted when `[auth]` carries no configured
# login provider, so the block stays discoverable even though `mode = "none"` needs
# none to function (issue #91 parses-and-carries this; #92 consumes it).
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
"""


class ConfigError(RuntimeError):
    """A runtime directory is missing its config — it was never initialized."""


@dataclass(frozen=True)
class PmSourceConfig:
    """One configured PM work source — a named, credentialed forge binding.

    ``name`` is the operator-chosen identity ingest tokens and board labels key on
    (conventionally the repo tail, e.g. ``blizzard`` for ``paul-gross/blizzard``);
    ``provider`` selects the adapter grammar (only ``github`` exists); ``repo`` is the
    ``owner/name`` coordinate the binding is pinned to; ``token_env`` names the
    environment variable carrying the credential — never the secret itself.
    ``api_base``/``web_base`` override the provider's default API/web origins (required
    to reach a self-hosted forge, e.g. GHE); ``web_base`` derives from ``api_base`` when
    omitted — the adapter's own knowledge, not this dataclass's.
    """

    name: str
    provider: str
    repo: str
    token_env: str
    api_base: str | None = None
    web_base: str | None = None


@dataclass(frozen=True)
class OAuthProviderConfig:
    """One configured OAuth login provider — parsed-and-carried by #91, *consumed*
    (secret resolution, ``type``/``issuer`` validation) by #92. ``client_secret_env``
    names the environment variable carrying the secret — never the secret itself,
    mirroring :class:`PmSourceConfig`'s ``token_env``."""

    name: str
    type: str
    display_name: str
    client_id: str
    client_secret_env: str
    issuer: str | None = None


@dataclass(frozen=True)
class AuthConfig:
    """Resolved ``[auth]`` config (issue #91) — the human-auth rollout knob.

    ``mode`` defaults to :data:`AUTH_MODE_NONE` and stays the shipped default until
    epic #89 completes. ``superuser`` (a nullable email) is parsed-and-carried here but
    consumed only by #94's bootstrap lifecycle. ``oauth_providers`` is parsed-and-carried
    here but consumed only by #92."""

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
    pm_sources: tuple[PmSourceConfig, ...] = ()
    runner_auth_mode: str = RUNNER_AUTH_WARN
    route_token_mode: str = ROUTE_TOKEN_WARN
    produces_mode: str = PRODUCES_WARN
    auth: AuthConfig = field(default_factory=AuthConfig)

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
            db_url=cls.default_db_url(root),
            host=os.environ.get(ENV_HOST, DEFAULT_HOST),
            port=int(os.environ.get(ENV_PORT, DEFAULT_PORT)),
        )

    def to_toml(self) -> str:
        lines = [
            "# blizzard-hub runtime configuration (blizzard hub init)\n",
            f'db_url = "{self.db_url}"\n',
            f'host = "{self.host}"\n',
            f"port = {self.port}\n",
            f'runner_auth_mode = "{self.runner_auth_mode}"\n',
            f'route_token_mode = "{self.route_token_mode}"\n',
            f'produces_mode = "{self.produces_mode}"\n',
        ]
        if not self.pm_sources:
            lines.append(_PM_SOURCE_EXAMPLE_COMMENT)
        for source in self.pm_sources:
            lines.append("\n[[pm_source]]\n")
            lines.append(f'name = "{source.name}"\n')
            lines.append(f'provider = "{source.provider}"\n')
            lines.append(f'repo = "{source.repo}"\n')
            lines.append(f'token_env = "{source.token_env}"\n')
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
        return "".join(lines)

    @classmethod
    def load(cls, root: Path, *, host: str | None = None, port: int | None = None) -> HubConfig:
        """Read a runtime root's config file; overlay CLI host/port when given."""
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
        return cls(
            root=root,
            db_url=str(raw["db_url"]),
            host=host or str(raw.get("host", DEFAULT_HOST)),
            port=port if port is not None else int(raw.get("port", DEFAULT_PORT)),
            pm_sources=_parse_pm_sources(raw.get("pm_source", [])),
            runner_auth_mode=runner_auth_mode,
            route_token_mode=route_token_mode,
            produces_mode=produces_mode,
            auth=_parse_auth(raw.get("auth", {})),
        )


def _parse_pm_sources(raw_sources: object) -> tuple[PmSourceConfig, ...]:
    """Validate and project ``[[pm_source]]`` entries; each rejection names
    the offending entry rather than failing generically."""
    if not isinstance(raw_sources, list):
        return ()
    sources: list[PmSourceConfig] = []
    seen_names: set[str] = set()
    seen_provider_repo: set[tuple[str, str]] = set()
    for entry in raw_sources:
        if not isinstance(entry, dict):
            raise ConfigError(f"[[pm_source]] entry must be a table, got {entry!r}")
        missing = [key for key in _REQUIRED_PM_SOURCE_KEYS if key not in entry]
        if missing:
            raise ConfigError(f"[[pm_source]] entry is missing required key(s) {missing}: {entry!r}")
        name = str(entry["name"])
        provider = str(entry["provider"])
        repo = str(entry["repo"])
        token_env = str(entry["token_env"])
        if ":" in name:
            # hub/cli.py's ingest-token grammar partitions on the first colon —
            # a colon in a source name breaks that split.
            raise ConfigError(f"[[pm_source]] name {name!r} must not contain ':'")
        if name in seen_names:
            raise ConfigError(f"duplicate [[pm_source]] name {name!r}")
        seen_names.add(name)
        provider_repo = (provider, repo)
        if provider_repo in seen_provider_repo:
            # Two names for one (provider, repo) would let the same item be ingested
            # twice under two identities — this is what holds pointer identity uniqueness
            # up, not a nicety.
            raise ConfigError(f"duplicate [[pm_source]] (provider, repo) {provider_repo!r} across two names")
        seen_provider_repo.add(provider_repo)
        if provider not in _KNOWN_PM_PROVIDERS:
            raise ConfigError(
                f"[[pm_source]] {name!r} has unknown provider {provider!r} (known: {sorted(_KNOWN_PM_PROVIDERS)})"
            )
        api_base = str(entry["api_base"]) if entry.get("api_base") else None
        web_base = str(entry["web_base"]) if entry.get("web_base") else None
        sources.append(
            PmSourceConfig(
                name=name, provider=provider, repo=repo, token_env=token_env, api_base=api_base, web_base=web_base
            )
        )
    return tuple(sources)


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
        providers.append(
            OAuthProviderConfig(
                name=name,
                type=str(entry["type"]),
                display_name=str(entry["display_name"]),
                client_id=str(entry["client_id"]),
                client_secret_env=str(entry["client_secret_env"]),
                issuer=str(issuer_raw) if issuer_raw else None,
            )
        )
    return tuple(providers)
