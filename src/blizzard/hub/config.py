"""Hub runtime configuration — resolved from a runtime directory.

``blizzard hub init <dir>`` scaffolds a config file and a data directory under a
runtime root; the daemon and the offline ``migrate`` verb read it back. The store
URL is the single portability knob (``bzh:sql-portable``): the sqlite default
lives under the data dir, and postgres is the same config with a different URL.
The bind port falls back to the winter service band's ``BZ_HUB_PORT`` (band +2).

``[[work_source]]`` is the zero-or-more configured work sources: each a
named, credentialed forge binding the composition root (``hub/work_sources/internal/factory.py``)
turns into one ``httpx.Client`` + adapter instance. ``tomllib`` parses the array of
tables for free; there is no stdlib TOML writer, so :meth:`HubConfig.to_toml` hand-rolls
the emit in the same string-concat style as the rest of this file.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from blizzard.foundation.forwarded import TrustedProxies

CONFIG_FILENAME = "blizzard-hub.toml"
DATA_DIRNAME = "data"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8421

ENV_HOST = "BZ_HUB_HOST"
ENV_PORT = "BZ_HUB_PORT"
# The container image (`bzh:manual-migrations`'s entrypoint) is the first consumer:
# a deployment varies the store URL by environment rather than baking one per image.
# Honored at load time identically by `hub host` and `hub migrate` — both resolve
# through `HubConfig.load`, so there is no per-verb wiring (`bzh:sql-portable`).
ENV_DB_URL = "BZ_HUB_DB_URL"

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

# The only work-source provider grammar a source may declare; an unknown provider fails
# at config load, not at first use.
_KNOWN_WORK_SOURCE_PROVIDERS = {"github"}
_REQUIRED_WORK_SOURCE_KEYS = ("name", "provider", "repo", "token_env")

# `[[work_source]]`'s pre-rename name (issue #55). Deliberately *not* aliased: a hub
# whose config still says `[[pm_source]]` parses as zero configured sources, which is a
# legal-looking hub whose every work-item read 503s and whose every board label renders
# null. Config is operator-owned and versionless, so the rename fails the daemon fast
# with a message naming the new key — the opposite call from the HTTP `/pm-items` alias,
# which stays because its clients can skew across deploys.
RENAMED_WORK_SOURCE_KEY = "pm_source"

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

# A fresh scaffold has no configured source, and without one `work-items` 503s and board
# pointer labels go null (you cannot render `{source}#{ref}` without a source name) — so
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
# api_base = "https://ghe.example.internal/api/v3"  # optional: override the API origin (e.g. GHE)
# web_base = "https://ghe.example.internal"          # optional: override the web origin; derives from api_base
"""

# Mirrors `_WORK_SOURCE_EXAMPLE_COMMENT` — emitted when `[auth]` carries no configured
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
# api_base = "https://ghe.example.internal"       # optional: override the provider's
#                                                  # default host (github type only)
"""


class ConfigError(RuntimeError):
    """A runtime directory is missing its config — it was never initialized."""


@dataclass(frozen=True)
class WorkSourceConfig:
    """One configured work source — a named, credentialed forge binding.

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
    #: Opt this source into the forge-status label sweep (issue #179) — default off,
    #: because dev/snapshot hubs in this workspace run against real forges and two
    #: writers must never fight over the same issues. Only the canonical instance for
    #: a repo should ever set this.
    annotate: bool = False
    api_base: str | None = None
    web_base: str | None = None


@dataclass(frozen=True)
class OAuthProviderConfig:
    """One configured OAuth login provider — parsed-and-carried by #91, *consumed*
    (secret resolution, ``type``/``issuer`` validation) by #92. ``client_secret_env``
    names the environment variable carrying the secret — never the secret itself,
    mirroring :class:`WorkSourceConfig`'s ``token_env``. ``api_base`` overrides the
    provider's default host (mirroring ``WorkSourceConfig.api_base``'s own GHE-override
    precedent) — unused by the ``oidc`` conformer (whose ``issuer`` already names its
    own host); the ``github`` conformer uses it to point both its authorize and API
    calls at a self-hosted/stub origin (e.g. the ``blizzard-mock`` stub IdP) instead of
    real GitHub."""

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
    work_sources: tuple[WorkSourceConfig, ...] = ()
    runner_auth_mode: str = RUNNER_AUTH_WARN
    route_token_mode: str = ROUTE_TOKEN_WARN
    produces_mode: str = PRODUCES_WARN
    #: The fleet-wide **follow-latest** default (issue #164): whether a chunk drifts to
    #: the newest enabled mint of its own graph's name at its next transition. ``False``
    #: — today's pin-by-id behavior — is the shipped default, so adopting the policy is
    #: a deliberate act. A graph's own ``follow_latest`` tri-state overrides this for
    #: chunks pinned to that mint; ``null`` there (every mint's default) inherits it.
    #: Not a `*_mode` string like the three above: those name a rollout ramp
    #: (off/warn/enforce), while this is a plain on/off with no intermediate state to
    #: warn about.
    follow_latest: bool = False
    #: The forge-status reconciler's sweep cadence, in seconds (issue #179) — a flat
    #: scalar following ``follow_latest``'s own precedent rather than a dedicated
    #: table. Only consulted when at least one ``[[work_source]]`` opts into
    #: ``annotate``; a hub with none starts no sweep loop regardless of this value.
    annotation_interval_seconds: int = 120
    auth: AuthConfig = field(default_factory=AuthConfig)
    #: The reverse-proxy trust set (issue #130) — proxy addresses or CIDRs whose
    #: ``X-Forwarded-Proto``/``X-Forwarded-For`` headers are honored (cookie ``Secure``
    #: flag, login-throttle key, auth-fact actor IP). Empty (the default) ignores those
    #: headers from every peer — behavior byte-identical to a direct-exposure deployment.
    #: Stored as raw strings that round-trip to toml; parsed into
    #: :class:`~blizzard.foundation.forwarded.TrustedProxies` at the composition root.
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
        lines = [
            "# blizzard-hub runtime configuration (blizzard hub init)\n",
            f'db_url = "{self.db_url}"\n',
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
    def load(cls, root: Path, *, host: str | None = None, port: int | None = None) -> HubConfig:
        """Read a runtime root's config file; overlay CLI host/port when given.

        ``db_url``/``host``/``port`` each resolve **CLI flag > environment > toml >
        default** (no CLI flag exists for ``db_url``) — see :data:`ENV_DB_URL`,
        :data:`ENV_HOST`, :data:`ENV_PORT`. Every variable unset leaves the resolved
        config byte-identical to a toml-only load.
        """
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
        return cls(
            root=root,
            db_url=os.environ.get(ENV_DB_URL) or str(raw["db_url"]),
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
            # hub/cli.py's ingest-token grammar partitions on the first colon —
            # a colon in a source name breaks that split.
            raise ConfigError(f"[[work_source]] name {name!r} must not contain ':'")
        if name in seen_names:
            raise ConfigError(f"duplicate [[work_source]] name {name!r}")
        seen_names.add(name)
        provider_repo = (provider, repo)
        if provider_repo in seen_provider_repo:
            # Two names for one (provider, repo) would let the same item be ingested
            # twice under two identities — this is what holds pointer identity uniqueness
            # up, not a nicety.
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
