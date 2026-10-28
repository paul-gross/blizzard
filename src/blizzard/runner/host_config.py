"""The host's configuration — ``blizzard-host.toml`` read, validated, and projected onto the
:class:`~blizzard.runner.config.RunnerConfig` every downstream reader takes.

``HostConfig.load`` sorts every error into the host's or one runner's. A host error refuses the file and
names every offending entry; a runner error halts that runner alone — :meth:`HostConfig.projections`
answers a :class:`HaltedRunner` for it and leaves its siblings projected."""

from __future__ import annotations

import json
import os
import re
import stat
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from blizzard.foundation.config_keys import ConfigRegistry, ConfigView, InvalidConfigValue
from blizzard.foundation.flags import (
    FlagRegistry,
    FlagResolution,
    HeldKey,
    held_keys,
    parse_overrides,
    resolve_for_keys,
)
from blizzard.foundation.forwarded import TrustedProxies
from blizzard.foundation.roles import collaborator, domain_model
from blizzard.foundation.runner_token_names import HOST_TOKEN_VARIABLE, hub_token_variable
from blizzard.runner.config import (
    DEFAULT_AUTH_HUB_ROLE,
    DEFAULT_BASE_BRANCH,
    DEFAULT_ENV_POOL,
    DEFAULT_HOST,
    DEFAULT_HUB_URL,
    DEFAULT_MAX_ENVIRONMENTS,
    DEFAULT_PORT,
    DEFAULT_RUNNER_NAME,
    DEFAULT_SESSION_SECRET_ENV,
    DEFAULT_WORKSPACE_ID,
    ENV_BASE_BRANCH,
    ENV_ENV_PASSTHROUGH,
    ENV_GATES,
    ENV_HOST,
    ENV_HUB_URL,
    ENV_PORT,
    ENV_PUBLIC_URL,
    ENV_RUNNER_PROMPT,
    ENV_TRANSCRIPTS_ROOT,
    ENV_WORKSPACE_ENVS,
    ENV_WORKSPACE_PROMPT,
    ENV_WORKSPACE_PROMPT_PACKAGE,
    ENV_WORKSPACE_ROOT,
    SCAFFOLD_AGENTS,
    SCAFFOLD_PROJECTS,
    Auth,
    Context,
    HostWorkspace,
    ProjectBinding,
    Queue,
    RunnerConfig,
    Spend,
    SubscriptionDeclaration,
    Throttle,
    Transcripts,
    WorkerStdout,
    _expanded_path_prepend,
    _parse_autonomy,
    _parse_harness_config_dir,
    _workspace_repos,
    effective_autonomy_source,
    resolve_session_secret,
)
from blizzard.runner.config_keys import HOST_REGISTRY, RUNNER_HOME_KEYS
from blizzard.runner.config_table import ConfigError, Table
from blizzard.runner.environments.factory import WORKSPACE_PROVIDERS
from blizzard.runner.harness.wiring import HarnessSections
from blizzard.runner.host_layout import (
    DATA_DIRNAME,
    HOST_CONFIG_FILENAME,
    LEGACY_CONFIG_FILENAME,
    Graduation,
    HostLayout,
    migrate_remedy,
)
from blizzard.runner.hub.token_file import ENV_FILENAME, assigned_token
from blizzard.runner.loop.mode import LoopMode
from blizzard.runner.public_origins import PublicOrigins
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC, SAMPLED_PROVIDERS
from blizzard.runner.throttle.settings import DAY_NAMES, HoldOrRelease, ThrottleSettings
from blizzard.runner.tracing.config import RunnerTracingConfig
from blizzard.runner.transcripts.caps import CHUNK_TRANSCRIPT_MAX_BYTES, TRANSCRIPT_RECORD_MAX_BYTES

#: The name a pre-host runner read its one token under; a host reads ``BZ_HUB_TOKEN_<NAME>`` per runner.
UNSUFFIXED_TOKEN_VARIABLE = "BZ_HUB_TOKEN"
#: The graduation procedure a directory that has not graduated is pointed at.
GRADUATION_PROCEDURE = "docs/upgrade.md"

#: What a scaffolded host declares for the plan today's scaffold synthesizes.
SCAFFOLD_SUBSCRIPTION = SubscriptionDeclaration(slug="anthropic", name="Anthropic", provider=PROVIDER_ANTHROPIC)


class _Placement(StrEnum):
    """Where graduation places a key of the flat runner file."""

    HOST = "host"
    WORKSPACE = "workspace"
    RUNNER = "runner"
    CONSUMED = "consumed"


@domain_model
@dataclass(frozen=True)
class _LegacyKey:
    """One flat-runner-file key's placement: where it goes, its name there when that is a ``[[workspace]]``, and —
    for a key with no home under a host — the prose naming what replaced it."""

    placement: _Placement
    home: str = ""
    retired: str = ""


#: Every flat-runner-file key graduation treats specially; a key absent here graduates into the ``[[runner]]`` as is.
#: The retired prose, the graduated maps and the top-level ``[[workspace]]`` refusals all derive from this one table.
_LEGACY_KEYS: Mapping[str, _LegacyKey] = {
    "hub_url": _LegacyKey(_Placement.HOST),
    "host": _LegacyKey(_Placement.HOST),
    "port": _LegacyKey(_Placement.HOST),
    "public_url": _LegacyKey(_Placement.HOST),
    "trusted_proxies": _LegacyKey(_Placement.HOST),
    "session_secret_env": _LegacyKey(_Placement.HOST),
    "auth": _LegacyKey(_Placement.HOST),
    "tracing": _LegacyKey(_Placement.HOST),
    "transcripts": _LegacyKey(_Placement.HOST),
    "worker_stdout": _LegacyKey(_Placement.HOST),
    "host_token": _LegacyKey(_Placement.HOST),
    "hub_tokens": _LegacyKey(_Placement.HOST),
    "subscription": _LegacyKey(_Placement.HOST),
    "workspace_id": _LegacyKey(_Placement.WORKSPACE, "id", "`[[workspace]] id`"),
    "workspace_root": _LegacyKey(_Placement.WORKSPACE, "root", "`[[workspace]] root`"),
    "workspace_provider": _LegacyKey(_Placement.WORKSPACE, "provider", "`[[workspace]] provider`"),
    "workspace_envs": _LegacyKey(_Placement.WORKSPACE, "envs", "`[[workspace]] envs`"),
    "workspace_repo": _LegacyKey(_Placement.WORKSPACE, "repo", "`[[workspace.repo]]`"),
    "workspace_prompt": _LegacyKey(_Placement.WORKSPACE, "prompt", "`[[workspace]] prompt`"),
    "workspace_prompt_file": _LegacyKey(_Placement.WORKSPACE, "prompt_file", "`[[workspace]] prompt_file`"),
    "workspace_prompt_package": _LegacyKey(_Placement.WORKSPACE, "prompt_package", "`[[workspace]] prompt_package`"),
    "max_environments": _LegacyKey(_Placement.WORKSPACE, "max_environments"),
    "base_branch": _LegacyKey(_Placement.WORKSPACE, "base_branch"),
    "db_url": _LegacyKey(_Placement.CONSUMED),
    "token_env": _LegacyKey(
        _Placement.CONSUMED, retired="a runner's token variable is derived from its name (BZ_HUB_TOKEN_<NAME>)"
    ),
    "runner_id": _LegacyKey(
        _Placement.CONSUMED, retired="the runner's name is `[[runner]] name` and its hub id is `[[runner]] id`"
    ),
    "name": _LegacyKey(_Placement.RUNNER),
    "projects": _LegacyKey(_Placement.RUNNER),
    "max_agents": _LegacyKey(_Placement.RUNNER, retired="`[[runner]] agents`"),
    "external_subscription_usage": _LegacyKey(_Placement.HOST, retired="a `[[subscription]]` the runner names"),
}

#: Keys of the flat runner file that have no home under a host, with where graduation places each.
_RETIRED_KEYS: Mapping[str, str] = {key: placed.retired for key, placed in _LEGACY_KEYS.items() if placed.retired}

#: The flat runner file's keys that graduation moves into its one ``[[workspace]]``, by their name there.
_GRADUATED_WORKSPACE_KEYS: Mapping[str, str] = {
    key: placed.home for key, placed in _LEGACY_KEYS.items() if placed.placement is _Placement.WORKSPACE
}

#: The flat runner file's keys that stay at the top of the host file, as they are.
_GRADUATED_HOST_KEYS = tuple(
    key for key, placed in _LEGACY_KEYS.items() if placed.placement is _Placement.HOST and not placed.retired
)


@domain_model
@dataclass(frozen=True)
class RunnerPathKey:
    """A key of a ``[[runner]]`` that names a file or directory: its path under the runner, such as
    ``("harness", "config_dir")``, and the parsed field it fills, as ``<class>.<field>``."""

    key: tuple[str, ...]
    field: str


#: Every ``[[runner]]`` path key, the harness sections' with the runner's: what ``runner ingest`` copies and rewrites.
RUNNER_PATH_KEYS: tuple[RunnerPathKey, ...] = (
    RunnerPathKey(("runner_prompt_file",), "RunnerConfig.runner_prompt_file"),
    RunnerPathKey(("transcripts_root",), "RunnerConfig.transcripts_root"),
    RunnerPathKey(("harness", "config_dir"), "RunnerConfig.harness_config_dir"),
    RunnerPathKey(("worker_settings_path",), "ClaudeCodeSection.worker_settings_path"),
    RunnerPathKey(("claude_code_credentials_path",), "ClaudeCodeSection.credentials_path"),
    RunnerPathKey(("opencode", "worker_config_path"), "OpenCodeSection.worker_config_path"),
    RunnerPathKey(("opencode", "auth_path"), "OpenCodeSection.auth_path"),
)

#: Keys that belong to a ``[[workspace]]``, so are refused at the top level: every graduated workspace key but its id.
_WORKSPACE_HOME_KEYS = tuple(home for home in _GRADUATED_WORKSPACE_KEYS.values() if home != "id")


class HostConfigError(ConfigError):
    """The host file is refused; :attr:`errors` names every offending entry."""

    def __init__(self, errors: tuple[str, ...]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))


class TokenSource(StrEnum):
    """Where a token resolved from, in the order it is looked for."""

    ENVIRONMENT = "environment"
    HOST_FILE = "host file"
    DOTENV = ".env"
    NONE = "none"


@domain_model
@dataclass(frozen=True)
class ResolvedToken:
    """A token and the source it came from."""

    value: str = field(default="", repr=False)
    source: TokenSource = TokenSource.NONE

    def rereads_dotenv(self) -> bool:
        """Whether a corrected ``.env`` takes effect without a restart: only when ``.env`` was the source."""
        return self.source is TokenSource.DOTENV


def resolve_token(
    variable: str, *, environ: Mapping[str, str], from_file: str = "", dotenv_text: str = ""
) -> ResolvedToken:
    """The first of the process environment's ``variable``, the host file's value, and the ``.env``'s
    assignment of ``variable``."""
    if value := environ.get(variable, ""):
        return ResolvedToken(value, TokenSource.ENVIRONMENT)
    if from_file:
        return ResolvedToken(from_file, TokenSource.HOST_FILE)
    if value := assigned_token(dotenv_text, variable):
        return ResolvedToken(value, TokenSource.DOTENV)
    return ResolvedToken()


@domain_model
@dataclass(frozen=True)
class RunnerEntry:
    """One ``[[runner]]`` as authored. ``own`` carries the tables only a runner owns, parsed onto a
    :class:`RunnerConfig` the host's keys are projected over; it is ``None`` when they did not parse."""

    name: str
    id: str | None = None
    subscription: str | None = None
    bindings: tuple[ProjectBinding, ...] = ()
    own: RunnerConfig | None = None
    #: The runner errors found reading the entry; :meth:`HostConfig.projections` adds the host-dependent ones.
    errors: tuple[str, ...] = ()
    #: What reading the entry earns a start-up warning for, such as a deprecated key.
    warnings: tuple[str, ...] = ()


@domain_model
@dataclass(frozen=True)
class BuiltRunner:
    """A runner whose entry projected cleanly: its hub id, the config every reader takes, and where its
    token resolved from, which decides whether the held token re-reads ``.env``."""

    id: str
    config: RunnerConfig
    token: ResolvedToken


@domain_model
@dataclass(frozen=True)
class HaltedRunner:
    """A runner left unbuilt by its own errors; the host serves without it."""

    name: str
    id: str | None
    errors: tuple[str, ...]

    def describe(self) -> str:
        """The runner as the start-up report, ``/api/ready`` and ``runner status`` name it: its name, id, and errors."""
        identity = self.id if self.id is not None else "no id"
        return f"runner {self.name} ({identity}) is halted: {'; '.join(self.errors)}"


@domain_model
@dataclass(frozen=True)
class HostConfig:
    """Resolved host configuration."""

    root: Path
    db_url: str
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    loop: LoopMode = LoopMode.TIMED
    hub_url: str = DEFAULT_HUB_URL
    public_urls: tuple[str, ...] = ()
    trusted_proxies: tuple[str, ...] = ()
    session_secret_env: str = DEFAULT_SESSION_SECRET_ENV
    session_secret: bytes = field(default=b"", repr=False)
    auth: Auth = field(default_factory=lambda: Auth.of(None))
    tracing: RunnerTracingConfig = field(default_factory=RunnerTracingConfig)
    #: The ``[flags]`` overrides, laid onto every runner's config.
    flags: Mapping[str, bool] = field(default_factory=dict)
    #: The ``[flags]`` as the load resolved them, applied by composition; ``None`` for a config built in code.
    flag_resolution: FlagResolution | None = None
    #: The guarded keys the file carried while their flag was off.
    held_keys: tuple[HeldKey, ...] = ()
    #: The flag registry the load resolved against; one built in code knows no flags unless given one.
    flag_registry: FlagRegistry = field(default_factory=FlagRegistry)
    #: Whether the load read the file strictly, which later loads in the same process repeat.
    strict: bool = False
    transcripts: Transcripts = field(default_factory=lambda: Transcripts.of(None))
    worker_stdout: WorkerStdout = field(default_factory=lambda: WorkerStdout.of(None))
    #: The host's own credential, resolved from the environment, then the host file, then ``.env``.
    host_token: ResolvedToken = field(default_factory=ResolvedToken)
    #: The host file's ``[hub_tokens]``, by runner name.
    hub_tokens: tuple[tuple[str, str], ...] = field(default=(), repr=False)
    subscriptions: tuple[SubscriptionDeclaration, ...] = ()
    workspaces: tuple[HostWorkspace, ...] = ()
    runners: tuple[RunnerEntry, ...] = ()
    #: Start-up warnings the file earns, as text.
    warnings: tuple[str, ...] = ()

    @property
    def layout(self) -> HostLayout:
        return HostLayout(self.root)

    @property
    def default_db_url(self) -> str:
        return self.layout.default_db_url

    @property
    def config_path(self) -> Path:
        return self.root / HOST_CONFIG_FILENAME

    @property
    def data_dir(self) -> Path:
        return self.root / DATA_DIRNAME

    def projections(self, environ: Mapping[str, str] | None = None) -> tuple[BuiltRunner | HaltedRunner, ...]:
        """Each ``[[runner]]`` as a built runner or a halted one, in file order. Tokens resolve from
        ``environ`` (the process environment by default), then the host file, then the directory's ``.env``."""
        source = os.environ if environ is None else environ
        return tuple(self._project(entry, source) for entry in self.runners)

    def token_for(self, entry: RunnerEntry, environ: Mapping[str, str] | None = None) -> ResolvedToken:
        """The token ``entry`` presents, resolved from ``environ`` (the process environment by default), the host
        file's ``[hub_tokens]``, then the directory's ``.env``."""
        return resolve_token(
            hub_token_variable(entry.name),
            environ=os.environ if environ is None else environ,
            from_file=dict(self.hub_tokens).get(entry.name, ""),
            dotenv_text=_dotenv_text(self.root),
        )

    def _problems(self, entry: RunnerEntry) -> list[str]:
        """The runner errors only the host's declarations show."""
        errors = list(entry.errors)
        declared = {subscription.slug for subscription in self.subscriptions}
        if entry.subscription is not None and entry.subscription not in declared:
            errors.append(f"names subscription {entry.subscription!r}, which the host does not declare")
        errors.extend(self._unsampled_levers(entry))
        workspaces = {workspace.id: workspace for workspace in self.workspaces}
        for binding in entry.bindings:
            if binding.workspace not in workspaces:
                errors.append(
                    f"binds project {binding.project!r} to workspace {binding.workspace!r}, "
                    "which the host does not declare"
                )
        return errors

    def _unsampled_levers(self, entry: RunnerEntry) -> list[str]:
        """The sample-judging levers (pace, utilization) a runner configures without a sampled subscription — they
        could only ever say no. An undeclared subscription is reported on its own."""
        settings = entry.own.throttle if entry.own is not None else None
        if settings is None:
            return []
        judging = [name for name in ("pace", "utilization") if getattr(settings, name) is not None]
        if not judging:
            return []
        tables = " and ".join(f"throttle.{name}" for name in judging)
        if entry.subscription is None:
            return [f"{tables} judge a subscription's samples, but the runner names no subscription"]
        declared = {subscription.slug: subscription for subscription in self.subscriptions}
        subscription = declared.get(entry.subscription)
        if subscription is None or subscription.provider in SAMPLED_PROVIDERS:
            return []
        return [
            f"{tables} judge a subscription's samples, but subscription {subscription.slug!r} "
            f"(provider {subscription.provider!r}) is never sampled"
        ]

    def _project(self, entry: RunnerEntry, environ: Mapping[str, str]) -> BuiltRunner | HaltedRunner:
        errors = self._problems(entry)
        if entry.id is None:
            errors.append("has no id")
        variable = hub_token_variable(entry.name)
        token = self.token_for(entry, environ)
        if not token.value:
            errors.append(
                f"has no hub token: set {variable}, a [hub_tokens] entry, or {variable} in {ENV_FILENAME} "
                "(`blizzard runner init` adds the runner; for one `blizzard hub runner list` already shows, "
                "`blizzard hub runner enroll <id>` prints the line to put there)"
                + self._unsuffixed_token_hint(variable, environ)
            )
        if errors or entry.id is None or entry.own is None:
            return HaltedRunner(entry.name, entry.id, tuple(errors))
        return BuiltRunner(entry.id, self._config_for(entry, entry.own, token), token)

    def _unsuffixed_token_hint(self, variable: str, environ: Mapping[str, str]) -> str:
        """The rename a leftover unsuffixed ``BZ_HUB_TOKEN`` calls for, or nothing when none is held: a pre-host
        runner read that name, and a host reads each runner's under its own."""
        if environ.get(UNSUFFIXED_TOKEN_VARIABLE) is not None:
            where = "the environment"
        elif assigned_token(_dotenv_text(self.root), UNSUFFIXED_TOKEN_VARIABLE):
            where = ENV_FILENAME
        else:
            return ""
        return f"; {UNSUFFIXED_TOKEN_VARIABLE} is set in {where} but a host reads {variable}: rename it to {variable}"

    def sole_runner(self, runner: str | None = None) -> RunnerEntry:
        """The ``[[runner]]`` an offline verb acts on: the one ``runner`` names by name or id, else the host's only
        one. Raises when it holds none, when ``runner`` names none it holds, or when it holds several and ``runner``
        names none of them."""
        if not self.runners:
            raise HostConfigError((f"{self.layout.config_path} declares no [[runner]]",))
        held = ", ".join(entry.name for entry in self.runners)
        if runner is not None:
            for entry in self.runners:
                if runner in (entry.name, entry.id):
                    return entry
            raise HostConfigError((f"{self.layout.config_path} declares no runner {runner!r}; it holds {held}",))
        first, *others = self.runners
        if others:
            raise HostConfigError(
                (f"{self.layout.config_path} declares several runners ({held}); name one with --runner",)
            )
        return first

    def check_new_runner(self, name: str) -> None:
        """Raises :class:`HostConfigError` when a runner called ``name`` could not join the ``[[runner]]`` entries this
        host holds — its name, or the token variable it reads, is already taken. It carries no id until the hub
        assigns one, so the id is not judged."""
        candidate = RunnerEntry(name=name)
        clashes = _repeats((*self.runners, candidate))
        if any(kind == "name" for kind, _ in clashes):
            raise HostConfigError((f"{self.config_path} already holds a runner named {name!r}; pick another name",))
        if clashes:
            variable = hub_token_variable(name)
            holder = next(entry.name for entry in self.runners if hub_token_variable(entry.name) == variable)
            raise HostConfigError(
                (
                    f"runner {name!r} would read its token from {variable}, which runner {holder!r} already reads; "
                    "pick another name",
                )
            )

    def runner_config(self, environ: Mapping[str, str] | None = None, *, runner: str | None = None) -> RunnerConfig:
        """The config of the runner :meth:`sole_runner` picks, for an offline verb: projected whether or not it has
        joined its hub — no id, no token — and refused only for the errors its own entry holds."""
        entry = self.sole_runner(runner)
        errors = self._problems(entry)
        if errors or entry.own is None:
            raise HostConfigError(tuple(f"runner {entry.name}: {error}" for error in errors))
        return self._config_for(entry, entry.own, self.token_for(entry, environ))

    def _config_for(
        self,
        entry: RunnerEntry,
        own: RunnerConfig,
        token: ResolvedToken,
    ) -> RunnerConfig:
        declared = {subscription.slug: subscription for subscription in self.subscriptions}
        chosen = (declared[entry.subscription],) if entry.subscription is not None else ()
        return replace(
            self._shared(own),
            name=entry.name,
            runner_id=entry.id,
            hub_token=token.value,
            projects=tuple(binding.project for binding in entry.bindings),
            bindings=entry.bindings,
            agents=own.agents,
            subscriptions=chosen,
        )

    def host_view(self) -> RunnerConfig:
        """The host's own keys on a :class:`RunnerConfig`, declaring every subscription — for the pieces a host
        builds before, or without, any runner: its listeners, tracing and workspaces."""
        base = RunnerConfig(root=self.root, db_url=self.db_url)
        view = self._shared(base)
        return replace(view, subscriptions=self.subscriptions)

    def _shared(self, base: RunnerConfig) -> RunnerConfig:
        """``base`` with the host's keys laid over it, its declared workspaces among them."""
        shared = replace(
            base,
            db_url=self.db_url,
            host=self.host,
            port=self.port,
            hub_url=self.hub_url,
            session_secret_env=self.session_secret_env,
            session_secret=self.session_secret,
            transcripts_ship=self.transcripts.ship,
            transcript_record_max_bytes=self.transcripts.record_max_bytes,
            transcript_chunk_max_bytes=self.transcripts.chunk_max_bytes,
            public_urls=self.public_urls,
            auth_superuser=self.auth.superuser,
            auth_hub_role_default=self.auth.hub_role_default,
            auth_users=self.auth.users,
            trusted_proxies=self.trusted_proxies,
            worker_stdout_retention_days=self.worker_stdout.retention_days,
            tracing=self.tracing,
            flags=self.flags,
            flag_resolution=self.flag_resolution,
            held_keys=self.held_keys,
            flag_registry=self.flag_registry,
            strict=self.strict,
            workspaces=self.workspaces or base.workspaces,
        )
        return shared

    @classmethod
    def load(
        cls,
        root: Path,
        *,
        host: str | None = None,
        port: int | None = None,
        loop: LoopMode | None = None,
        flag_registry: FlagRegistry,
        strict: bool,
        key_registry: ConfigRegistry | None = None,
    ) -> HostConfig:
        """Read a host directory's ``blizzard-host.toml``; overlay CLI host/port when given. A directory
        holding only the pre-host file, an uninitialised one, and every host error are refused."""
        root = root.resolve()
        layout = HostLayout(root)
        match layout.graduation():
            case Graduation.UNGRADUATED:
                raise HostConfigError(
                    (
                        f"{root} holds {LEGACY_CONFIG_FILENAME} and no {HOST_CONFIG_FILENAME}: it has not "
                        f"graduated to a host; follow the graduation procedure in {GRADUATION_PROCEDURE}",
                    )
                )
            case Graduation.UNINITIALISED:
                raise HostConfigError((f"{root} is not an initialized host (run `blizzard runner init {root}`)",))
            case Graduation.GRADUATED:
                pass
        path = layout.config_path
        try:
            raw = decode_host_text(path.read_text(), what=str(path))
        except (OSError, ConfigError) as exc:
            raise HostConfigError((f"{path} cannot be read: {exc}",)) from exc
        try:
            return _parse(
                raw,
                layout=layout,
                host=host,
                port=port,
                loop=loop,
                flag_registry=flag_registry,
                strict=strict,
                key_registry=key_registry,
            )
        except InvalidConfigValue as exc:
            raise HostConfigError((str(exc),)) from exc

    @classmethod
    def load_for_store(
        cls,
        root: Path,
        *,
        host: str | None = None,
        port: int | None = None,
        loop: LoopMode | None = None,
        flag_registry: FlagRegistry,
        strict: bool,
    ) -> HostConfig:
        """:meth:`load`, for a verb that opens the store without creating it: where the store is the default SQLite
        file and that file is absent, the directory is refused, naming ``migrate`` — which alone creates it, and
        adopts a pre-host ``runner.db`` first. A store named by ``db_url`` is left to the engine."""
        config = cls.load(root, host=host, port=port, loop=loop, flag_registry=flag_registry, strict=strict)
        layout = config.layout
        if config.db_url != layout.default_db_url or layout.store_path.exists():
            return config
        config.refuse_unadopted()
        migrate = migrate_remedy(layout.root)
        raise HostConfigError(
            (
                f"{layout.root} holds no {layout.store_path.relative_to(layout.root)}: run `{migrate}` (or "
                f"`blizzard runner init {layout.root}`) to create it",
            )
        )

    def refuse_unadopted(self) -> None:
        """Refuse a directory whose default store is absent while the pre-host ``runner.db`` exists, naming ``migrate``
        — which alone adopts it. A store named by ``db_url``, or a default store that exists, is left alone."""
        layout = self.layout
        if self.db_url != layout.default_db_url or layout.store_path.exists() or not layout.legacy_store_path.exists():
            return
        raise HostConfigError(
            (
                f"{layout.root} holds {layout.legacy_store_path.relative_to(layout.root)} from a runner before "
                f"hosts and no {layout.store_path.relative_to(layout.root)}: run `{migrate_remedy(layout.root)}` "
                f"first to adopt it (see {GRADUATION_PROCEDURE})",
            )
        )

    @classmethod
    def graduated(
        cls,
        root: Path,
        legacy: Mapping[str, Any],
        *,
        runner_id: str,
        served: tuple[str, ...],
        flag_registry: FlagRegistry,
        strict: bool,
    ) -> HostConfig:
        """The host a pre-host runner directory at ``root`` graduates to, from its ``blizzard-runner.toml`` as
        ``legacy`` holds it — by graduation's key placement (``GRADUATION_PROCEDURE``): its ``[[runner]]`` takes the
        hub id its store records, ``runner_id``, and binds each project ``served`` when the file names none. Every
        host error is refused."""
        layout = HostLayout(root.resolve())
        return _parse(
            _graduated_raw(legacy, runner_id=runner_id, served=served),
            layout=layout,
            host=None,
            port=None,
            loop=None,
            flag_registry=flag_registry,
            strict=strict,
        )

    @classmethod
    def scaffold(cls, root: Path, *, hub_url: str | None = None, projects: tuple[str, ...] | None = None) -> HostConfig:
        """The default host for a fresh directory (used by ``init``): today's runner scaffold, graduated —
        one ``[[workspace]]`` and one ``[[runner]]`` seeded from the injected ``BZ_*`` environment, naming
        the one ``[[subscription]]`` the old default synthesized. The runner has no ``id`` until it joins."""
        layout = HostLayout(root)
        environ = os.environ
        workspace_root = environ.get(ENV_WORKSPACE_ROOT, "")
        envs = environ.get(ENV_WORKSPACE_ENVS)
        workspace = HostWorkspace(
            id=DEFAULT_WORKSPACE_ID,
            root=workspace_root,
            provider="winter" if workspace_root else "basic",
            envs=tuple(e.strip() for e in envs.split(",") if e.strip()) if envs else DEFAULT_ENV_POOL,
            base_branch=environ.get(ENV_BASE_BRANCH, DEFAULT_BASE_BRANCH),
            prompt=environ.get(ENV_WORKSPACE_PROMPT, ""),
            prompt_package=environ.get(ENV_WORKSPACE_PROMPT_PACKAGE, ""),
        )
        entry = scaffold_runner(
            root,
            DEFAULT_RUNNER_NAME,
            subscription=SCAFFOLD_SUBSCRIPTION.slug,
            bindings=tuple(
                ProjectBinding(project, workspace.id)
                for project in (projects if projects is not None else SCAFFOLD_PROJECTS)
            ),
        )
        return cls(
            root=root,
            db_url=layout.default_db_url,
            host=environ.get(ENV_HOST, DEFAULT_HOST),
            port=int(environ.get(ENV_PORT, DEFAULT_PORT)),
            hub_url=hub_url or environ.get(ENV_HUB_URL, DEFAULT_HUB_URL),
            public_urls=PublicOrigins.entries(environ.get(ENV_PUBLIC_URL, "").split(","), ConfigError),
            subscriptions=(SCAFFOLD_SUBSCRIPTION,),
            workspaces=(workspace,),
            runners=(entry,),
        )

    def to_toml(self) -> str:
        """The file text: host keys, host tables, then each subscription, workspace, and runner. The store
        URL is omitted when it is the default, as is any value only a hand edit would carry."""
        lines = [
            "# blizzard host configuration (blizzard runner init)\n",
            f"hub_url = {json.dumps(self.hub_url)}\n",
            f"host = {json.dumps(self.host)}\n",
            f"port = {self.port}\n",
        ]
        if self.db_url != self.default_db_url:
            lines.append(f"db_url = {json.dumps(self.db_url)}\n")
        if self.public_urls:
            lines.append(f"public_url = {_toml_origins(self.public_urls)}\n")
        if self.trusted_proxies:
            lines.append(f"trusted_proxies = {json.dumps(list(self.trusted_proxies))}\n")
        lines.append(f"session_secret_env = {json.dumps(self.session_secret_env)}\n")
        if self.host_token.source is TokenSource.HOST_FILE:
            lines.append(f"host_token = {json.dumps(self.host_token.value)}\n")
        lines.extend(self._host_tables())
        for subscription in self.subscriptions:
            lines.append(_subscription_toml(subscription))
        for workspace in self.workspaces:
            lines.append(_workspace_toml(workspace))
        for entry in self.runners:
            lines.append(_runner_toml(entry))
        return "".join(lines)

    def _host_tables(self) -> list[str]:
        lines: list[str] = []
        auth = self.auth
        if auth.superuser or auth.hub_role_default != DEFAULT_AUTH_HUB_ROLE or auth.users:
            lines.append("\n[auth]\n")
            if auth.superuser:
                lines.append(f"superuser = {json.dumps(auth.superuser)}\n")
            lines.append(f"hub_role_default = {json.dumps(auth.hub_role_default)}\n")
            if auth.users:
                lines.append("\n[auth.users]\n")
                lines.extend(f"{json.dumps(name)} = {json.dumps(role)}\n" for name, role in auth.users)
        lines.extend(self.tracing.to_toml(unit="closed leases"))
        lines.append(_transcripts_toml(self.transcripts))
        if self.worker_stdout.table.body:
            lines.append("\n[worker_stdout]\n")
            lines.extend(f"{key} = {_toml_scalar(value)}\n" for key, value in self.worker_stdout.table.body.items())
        if self.hub_tokens:
            lines.append("\n[hub_tokens]\n")
            lines.extend(f"{json.dumps(name)} = {json.dumps(token)}\n" for name, token in self.hub_tokens)
        return lines


def scaffold_runner(
    root: Path,
    name: str,
    *,
    bindings: tuple[ProjectBinding, ...],
    subscription: str | None = None,
    runner_id: str | None = None,
    agents: int | None = None,
) -> RunnerEntry:
    """A ``[[runner]]`` carrying the scaffold's runner defaults, seeded from the injected ``BZ_*`` environment:
    ``init``'s first runner, and each one ``runner add`` appends."""
    environ = os.environ
    gates = environ.get(ENV_GATES)
    passthrough = environ.get(ENV_ENV_PASSTHROUGH, "")
    own = RunnerConfig(
        root=root,
        db_url=HostLayout(root).default_db_url,
        agents=SCAFFOLD_AGENTS if agents is None else agents,
        gates=tuple(g.strip() for g in gates.split(",") if g.strip()) if gates else (),
        harness_sections=HarnessSections.scaffold(root, environ),
        runner_prompt=environ.get(ENV_RUNNER_PROMPT, ""),
        transcripts_root=environ.get(ENV_TRANSCRIPTS_ROOT, ""),
        worker_env_passthrough=tuple(n.strip() for n in passthrough.split(",") if n.strip()),
    )
    return RunnerEntry(name=name, id=runner_id, subscription=subscription, bindings=bindings, own=own)


def runner_toml(entry: RunnerEntry) -> str:
    """``entry`` as the ``[[runner]]`` table text the host file holds, its sub-tables beneath it."""
    return _runner_toml(entry)


def workspace_toml(workspace: HostWorkspace) -> str:
    """``workspace`` as the ``[[workspace]]`` table text the host file holds, its repos beneath it."""
    return _workspace_toml(workspace)


def subscription_toml(subscription: SubscriptionDeclaration) -> str:
    """``subscription`` as the ``[[subscription]]`` table text the host file holds."""
    return _subscription_toml(subscription)


def _dotenv_text(root: Path) -> str:
    try:
        return (root / ENV_FILENAME).read_text()
    except FileNotFoundError:
        return ""


@collaborator
@dataclass
class _Errors:
    """The errors a read has found so far; :meth:`attempt` runs one step and records its failure."""

    found: list[str] = field(default_factory=list)

    def add(self, message: str) -> None:
        self.found.append(message)

    def attempt[T](self, label: str, step: Callable[[], T], fallback: T) -> T:
        try:
            return step()
        except (ConfigError, ValueError, TypeError) as exc:
            self.found.append(f"{label}: {exc}")
            return fallback


def _parse_loop(value: object) -> LoopMode:
    if isinstance(value, str) and value in set(LoopMode):
        return LoopMode(value)
    raise ConfigError(f"loop must be one of {[str(mode) for mode in LoopMode]}, got {value!r}")


def _parse(
    raw: dict[str, Any],
    *,
    layout: HostLayout,
    host: str | None,
    port: int | None,
    loop: LoopMode | None,
    flag_registry: FlagRegistry,
    strict: bool,
    key_registry: ConfigRegistry | None = None,
) -> HostConfig:
    errors = _Errors()
    root = layout.root
    path = layout.config_path
    registry = key_registry or HOST_REGISTRY
    document = ConfigView.root(registry, raw, strict=strict)
    # Resolved first: every key read below that a flag guards reads against it.
    resolved = errors.attempt("[flags]", lambda: resolve_for_keys(document, "runner", flag_registry, ConfigError), None)
    resolution: FlagResolution | None = None
    if resolved is not None:
        document, resolution = resolved
    _refuse_misplaced_keys(document, errors)
    session_secret_env = str(document.get("session_secret_env", DEFAULT_SESSION_SECRET_ENV))
    session_secret = errors.attempt("session_secret_env", lambda: resolve_session_secret(session_secret_env), b"")
    transcripts = Transcripts.of(document.table("transcripts"))
    errors.attempt(
        "[transcripts]", lambda: (transcripts.ship, transcripts.record_max_bytes, transcripts.chunk_max_bytes), None
    )
    worker_stdout = WorkerStdout.of(document.table("worker_stdout"))
    errors.attempt("[worker_stdout]", lambda: worker_stdout.retention_days, 0)
    subscriptions = errors.attempt("[[subscription]]", lambda: SubscriptionDeclaration.declared(document), ())
    workspaces = _workspaces(document, root=root, errors=errors)
    db_url = str(document.get("db_url")) if "db_url" in document else layout.default_db_url
    if not db_url.strip():
        errors.add("db_url must not be blank")
    resolved_port = errors.attempt(
        "port", lambda: port if port is not None else int(document.get("port", DEFAULT_PORT)), DEFAULT_PORT
    )
    resolved_loop = errors.attempt(
        "loop",
        lambda: loop if loop is not None else _parse_loop(document.get("loop", LoopMode.TIMED.value)),
        LoopMode.TIMED,
    )
    public_urls = errors.attempt(
        "public_url", lambda: PublicOrigins.entries(document.get("public_url"), ConfigError), ()
    )
    trusted_proxies = errors.attempt(
        "trusted_proxies", lambda: TrustedProxies.entries(document.get("trusted_proxies"), ConfigError), ()
    )
    tracing = errors.attempt(
        "[tracing]", lambda: RunnerTracingConfig.of(document.table("tracing"), ConfigError), RunnerTracingConfig()
    )
    flags: Mapping[str, bool] = {}
    if resolved is not None:
        flags = parse_overrides(document, ConfigError)
    if not isinstance(document.get("hub_tokens", {}), dict):
        errors.add("[hub_tokens] must be a table of tokens keyed by runner name")
    hub_tokens = Table.of(document).pairs("hub_tokens")
    host_token = resolve_token(
        HOST_TOKEN_VARIABLE,
        environ=os.environ,
        from_file=str(document.get("host_token", "")),
        dotenv_text=_dotenv_text(root),
    )
    entries = _runner_entries(document, root=root, db_url=db_url, path=path, errors=errors)
    _check_runner_uniqueness(entries, has_host_token=bool(host_token.value), errors=errors)
    if errors.found:
        raise HostConfigError(tuple(errors.found))
    held = held_keys(registry, raw, resolution.flags) if resolution is not None else ()
    return HostConfig(
        root=root,
        db_url=db_url,
        host=host or str(document.get("host", DEFAULT_HOST)),
        port=resolved_port,
        loop=resolved_loop,
        hub_url=str(document.get("hub_url", DEFAULT_HUB_URL)),
        public_urls=public_urls,
        trusted_proxies=trusted_proxies,
        session_secret_env=session_secret_env,
        session_secret=session_secret,
        auth=Auth.of(document.table("auth")),
        tracing=tracing,
        flags=flags,
        flag_resolution=resolution,
        held_keys=held,
        flag_registry=flag_registry,
        strict=strict,
        transcripts=transcripts,
        worker_stdout=worker_stdout,
        host_token=host_token,
        hub_tokens=hub_tokens,
        subscriptions=subscriptions,
        workspaces=workspaces,
        runners=entries,
        warnings=(
            *_permission_warnings(path, holds_credentials="host_token" in document or bool(hub_tokens)),
            *(f"{entry.key} is ignored while the flag {entry.flag} is off" for entry in held),
            *(warning for entry in entries for warning in entry.warnings),
        ),
    )


#: The agent count a flat runner file declaring no ``max_agents`` ran with.
_LEGACY_MAX_AGENTS = 1


def decode_host_text(text: str, *, what: str) -> dict[str, Any]:
    """``text`` — a host file, a table to append to one, or a TOML literal — decoded to the mapping it holds: the one
    decode step every read of host-file text goes through. :class:`ConfigError`, naming ``what``, when it is not
    TOML."""
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{what} is not valid TOML: {error}") from error


def read_legacy_document(layout: HostLayout) -> dict[str, Any]:
    """The pre-host ``blizzard-runner.toml`` of the runner directory ``layout`` names, decoded — the document
    :meth:`HostConfig.graduated` takes. :class:`ConfigError` when it cannot be read or is not TOML."""
    path = layout.legacy_config_path
    try:
        text = path.read_text()
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}") from error
    return decode_host_text(text, what=str(path))


def _graduated_raw(legacy: Mapping[str, Any], *, runner_id: str, served: tuple[str, ...]) -> dict[str, Any]:
    """The host file a flat runner file graduates to, as parsed TOML: the host keys at the top, its ``workspace_*``
    and prompt keys as one ``[[workspace]]``, and everything else as one ``[[runner]]``."""
    raw: dict[str, Any] = {key: legacy[key] for key in _GRADUATED_HOST_KEYS if key in legacy}
    workspace = {home: legacy[key] for key, home in _GRADUATED_WORKSPACE_KEYS.items() if key in legacy}
    workspace.setdefault("id", DEFAULT_WORKSPACE_ID)
    runner: dict[str, Any] = {key: value for key, value in legacy.items() if key not in _LEGACY_KEYS}
    runner["id"] = runner_id
    runner["name"] = legacy.get("name", legacy.get("runner_id", DEFAULT_RUNNER_NAME))
    runner["agents"] = legacy.get("max_agents", _LEGACY_MAX_AGENTS)
    projects = legacy["projects"] if "projects" in legacy else list(served)
    runner["projects"] = [{"project": project, "workspace": workspace["id"]} for project in projects]
    declared = legacy.get("subscription")
    if declared is None:
        usage = legacy.get("external_subscription_usage", {})
        synthesized = {
            "slug": SCAFFOLD_SUBSCRIPTION.slug,
            "name": SCAFFOLD_SUBSCRIPTION.name,
            "provider": SCAFFOLD_SUBSCRIPTION.provider,
            **{key: usage[key] for key in ("credentials_path", "sample_interval_seconds") if key in usage},
        }
        raw["subscription"] = [synthesized]
        runner["subscription"] = SCAFFOLD_SUBSCRIPTION.slug
    elif isinstance(declared, list) and len(declared) == 1 and isinstance(declared[0], dict):
        runner["subscription"] = declared[0].get("slug")
    elif isinstance(declared, list) and declared:
        # Which plan the runner spends is the one its enabled harness spends — a choice no key records.
        raise ConfigError(
            f"the runner file declares {len(declared)} [[subscription]]s, and which one the runner spends is not "
            f"recorded: graduate it by hand ({GRADUATION_PROCEDURE})"
        )
    raw["workspace"] = [workspace]
    raw["runner"] = [runner]
    return raw


def _refuse_misplaced_keys(raw: ConfigView, errors: _Errors) -> None:
    """Top-level keys with no home under a host, and keys whose home is a ``[[runner]]`` or
    ``[[workspace]]``, each named with where it belongs."""
    for key, home in _RETIRED_KEYS.items():
        if key in raw:
            errors.add(f"{key!r} has no home under a host: it is {home}")
    for key in RUNNER_HOME_KEYS:
        if key in raw:
            errors.add(f"{key!r} belongs under [[runner]], not at the top level")
    for key in _WORKSPACE_HOME_KEYS:
        if key in raw:
            errors.add(f"{key!r} belongs under [[workspace]], not at the top level")


def _entries(document: ConfigView, table: str, errors: _Errors) -> list[ConfigView]:
    raw = document.get(table, [])
    if not isinstance(raw, list):
        errors.add(f"[[{table}]] must be an array of tables")
        return []
    entries: list[ConfigView] = []
    for position, entry in enumerate(raw, start=1):
        if isinstance(entry, dict):
            entries.append(document.element(table, entry))
        else:
            errors.add(f"[[{table}]] #{position} must be a table")
    return entries


def _workspaces(document: ConfigView, *, root: Path, errors: _Errors) -> tuple[HostWorkspace, ...]:
    workspaces: list[HostWorkspace] = []
    for position, entry in enumerate(_entries(document, "workspace", errors), start=1):
        label = (
            f"[[workspace]] {entry.get('id')!r}" if isinstance(entry.get("id"), str) else f"[[workspace]] #{position}"
        )
        workspace = errors.attempt(label, lambda entry=entry: _workspace(entry), None)
        if workspace is None:
            continue
        if any(other.id == workspace.id for other in workspaces):
            errors.add(f"{label}: duplicate [[workspace]] id {workspace.id!r}")
            continue
        resolved = workspace.settings(root).effective_workspace_root
        for other in workspaces:
            if other.settings(root).effective_workspace_root != resolved:
                continue
            shared = sorted(set(other.envs) & set(workspace.envs))
            if workspace.provider == other.provider == "basic":
                # A basic pool owns its root's managed directories outright, so a second one there evicts the first's.
                errors.add(f"{label}: is a basic workspace on root {resolved!r}, as is workspace {other.id!r}")
            elif shared:
                errors.add(
                    f"{label}: shares root {resolved!r} with workspace {other.id!r} "
                    f"and both name environment(s) {', '.join(shared)}"
                )
        workspaces.append(workspace)
    return tuple(workspaces)


def _workspace(entry: ConfigView) -> HostWorkspace:
    identity = entry.get("id")
    if not isinstance(identity, str) or not identity.strip():
        raise ConfigError("id must be a non-blank string")
    provider = entry.get("provider", "winter")
    if provider not in WORKSPACE_PROVIDERS:
        names = " or ".join(repr(name) for name in WORKSPACE_PROVIDERS)
        raise ConfigError(f"provider must be {names}, got {provider!r}")
    cap = entry.get("max_environments", DEFAULT_MAX_ENVIRONMENTS)
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise ConfigError(f"max_environments must be a positive integer, got {cap!r}")
    envs = entry.get("envs", DEFAULT_ENV_POOL)
    if not isinstance(envs, (list, tuple)):
        raise ConfigError(f"envs must be a list of environment names, got {envs!r}")
    return HostWorkspace(
        id=identity.strip(),
        root=str(entry.get("root", "")),
        provider=provider,
        envs=tuple(str(env) for env in envs),
        max_environments=cap,
        repos=_workspace_repos(entry),
        base_branch=str(entry.get("base_branch", DEFAULT_BASE_BRANCH)),
        prompt=str(entry.get("prompt", "")),
        prompt_file=str(entry.get("prompt_file", "")),
        prompt_package=str(entry.get("prompt_package", "")),
    )


def _runner_entries(
    document: ConfigView, *, root: Path, db_url: str, path: Path, errors: _Errors
) -> tuple[RunnerEntry, ...]:
    entries: list[RunnerEntry] = []
    for position, entry in enumerate(_entries(document, "runner", errors), start=1):
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.add(f"[[runner]] #{position} has no name")
            continue
        entries.append(_runner_entry(entry, name.strip(), root=root, db_url=db_url, path=path))
    return tuple(entries)


def _runner_entry(raw: ConfigView, name: str, *, root: Path, db_url: str, path: Path) -> RunnerEntry:
    problems = _Errors()
    identity = raw.get("id")
    if "id" in raw and (not isinstance(identity, str) or not identity.strip()):
        problems.add(f"id must be a non-blank string, got {identity!r}")
        identity = None
    subscription = raw.get("subscription")
    if subscription is not None and not isinstance(subscription, str):
        problems.add(f"subscription must name a [[subscription]] slug, got {subscription!r}")
        subscription = None
    for key, home in _RETIRED_KEYS.items():
        if key in raw:
            problems.add(f"{key!r} has no home under a host: it is {home}")
    bindings = _bindings(raw, problems)
    warnings: list[str] = []
    agents = _runner_agents(raw, bindings, problems, warnings)
    own = problems.attempt(
        "its tables",
        lambda: replace(_own_tables(raw, root=root, db_url=db_url, path=path), agents=agents),
        None,
    )
    return RunnerEntry(
        name=name,
        id=identity.strip() if isinstance(identity, str) else None,
        subscription=subscription,
        bindings=bindings,
        own=own,
        errors=tuple(problems.found),
        warnings=tuple(f"runner {name}: {warning}" for warning in warnings),
    )


def _runner_agents(raw: ConfigView, bindings: tuple[ProjectBinding, ...], errors: _Errors, warnings: list[str]) -> int:
    """The runner's one agent count: its own ``agents`` when set, else the sum of its bindings' deprecated ``agents``
    — an error when some binding declares none."""
    declared = [binding.agents for binding in bindings if binding.agents is not None]
    if "agents" in raw:
        agents = raw.get("agents")
        if isinstance(agents, bool) or not isinstance(agents, int) or agents < 1:
            errors.add(f"agents must be a positive integer, got {agents!r}")
            return 0
        if declared:
            warnings.append("agents is set, so the per-binding `agents` in `projects` are ignored")
        return agents
    if len(declared) < len(bindings):
        errors.add("has no agents: set `agents` on the runner")
        return 0
    if declared:
        warnings.append("the per-binding `agents` in `projects` is deprecated: set `agents` on the runner")
    return sum(declared)


def _bindings(raw: ConfigView, errors: _Errors) -> tuple[ProjectBinding, ...]:
    if "projects" not in raw:
        errors.add("has no projects: list the projects it serves, or `projects = []` to serve none")
        return ()
    listed = raw.get("projects")
    if not isinstance(listed, list):
        errors.add(f"projects must be a list of {{ project, workspace }} bindings, got {listed!r}")
        return ()
    bindings: list[ProjectBinding] = []
    for binding in listed:
        entry = raw.element("projects", binding)
        project = entry.get("project") if isinstance(binding, dict) else None
        workspace = entry.get("workspace") if isinstance(binding, dict) else None
        agents = entry.get("agents") if isinstance(binding, dict) else None
        if (
            not isinstance(project, str)
            or not project.strip()
            or not isinstance(workspace, str)
            or not workspace.strip()
        ):
            errors.add(f"projects entry {binding!r} must name a project and a workspace")
            continue
        label = f"binding of project {project.strip()!r}"
        if agents is not None and (isinstance(agents, bool) or not isinstance(agents, int) or agents < 1):
            errors.add(f"{label}: agents must be a positive integer, got {agents!r}")
            continue
        if any(bound.project == project.strip() for bound in bindings):
            errors.add(f"{label}: project {project.strip()!r} is bound more than once")
            continue
        bindings.append(ProjectBinding(project.strip(), workspace.strip(), agents))
    return tuple(bindings)


def _own_tables(raw: ConfigView, *, root: Path, db_url: str, path: Path) -> RunnerConfig:
    """The tables only a runner owns, parsed onto a :class:`RunnerConfig` the host's keys are projected over."""
    sections = HarnessSections.parse(raw, root=root, path=path)
    harness = Table.of(raw.table("harness"))
    gates = raw.get("gates", [])
    if not isinstance(gates, list):
        raise ConfigError(f"gates must be a list of node names, got {gates!r}")
    spend = Spend.of(raw.table("cost"))
    throttle = Throttle.of(raw.table("throttle"))
    context = Context.of(raw.table("context"))
    worker = Table.of(raw.table("worker"))
    return RunnerConfig(
        root=root,
        db_url=db_url,
        harness_sections=sections,
        autonomy=_parse_autonomy(harness.get("autonomy"), path),
        autonomy_source=effective_autonomy_source(sections, "autonomy" in harness.view),
        harness_config_dir=_parse_harness_config_dir(harness.get("config_dir"), root, path),
        gates=tuple(str(gate) for gate in gates),
        runner_prompt=str(raw.get("runner_prompt", "")),
        runner_prompt_file=str(raw.get("runner_prompt_file", "")),
        transcripts_root=str(raw.get("transcripts_root", "")),
        queue_strict=Queue.of(raw.table("queue")).strict,
        chunk_cap_usd=spend.chunk_cap_usd,
        runner_ceiling_usd=spend.ceiling_usd,
        runner_ceiling_window_hours=spend.window_hours,
        throttle=throttle.settings(ceiling_usd=spend.ceiling_usd, ceiling_window_hours=spend.window_hours),
        context_warn_tokens=context.warn_tokens,
        context_sample_interval_seconds=context.sample_interval_seconds,
        worker_env_passthrough=worker.names("env_passthrough"),
        worker_path_prepend=_expanded_path_prepend(worker.names("path_prepend")),
    )


def _check_runner_uniqueness(entries: tuple[RunnerEntry, ...], *, has_host_token: bool, errors: _Errors) -> None:
    """The host errors only the whole set of runners shows: a repeated id, name, or token variable, and more
    than one runner on a host with no host token."""
    errors.found.extend(f"two [[runner]] entries share the {kind} {key!r}" for kind, key in _repeats(entries))
    if len(entries) > 1 and not has_host_token:
        errors.add(f"more than one [[runner]] needs a host token ({HOST_TOKEN_VARIABLE}), and none is set")


def _repeats(entries: tuple[RunnerEntry, ...]) -> list[tuple[str, str]]:
    """Each ``(kind, key)`` two entries share, over the keys that decide whether runners clash."""
    repeats: list[tuple[str, str]] = []
    for kind, keys in (
        ("id", [entry.id for entry in entries]),
        ("name", [entry.name for entry in entries]),
        ("token variable", [hub_token_variable(entry.name) for entry in entries]),
    ):
        repeats.extend((kind, key) for key in sorted({key for key in keys if key is not None and keys.count(key) > 1}))
    return repeats


def _permission_warnings(path: Path, *, holds_credentials: bool) -> tuple[str, ...]:
    if not holds_credentials or not path.exists() or not path.stat().st_mode & (stat.S_IRGRP | stat.S_IROTH):
        return ()
    return (f"{path} holds credentials and is readable by others; restrict it to its owner (chmod 600)",)


def _transcripts_toml(transcripts: Transcripts) -> str:
    """The ``[transcripts]`` table: the lane's switch, then each byte ceiling live once overridden and commented at
    its default otherwise, so the scaffolded file shows an operator what each ceiling is and how the two order."""
    caps = "".join(
        f"{key} = {value}\n" if value is not None else f"# {key} = {default}\n"
        for key, value, default in (
            ("record_max_bytes", transcripts.record_max_bytes, TRANSCRIPT_RECORD_MAX_BYTES),
            ("chunk_max_bytes", transcripts.chunk_max_bytes, CHUNK_TRANSCRIPT_MAX_BYTES),
        )
    )
    return (
        "\n# The transcript outbound lane — off by default; the hub's own\n"
        "# durable, compressed-at-rest segment store is already landed, so\n"
        "# turning this on is a rollout decision, not a bandwidth-for-nothing one.\n"
        "[transcripts]\n"
        f"ship = {'true' if transcripts.ship else 'false'}\n"
        "# This lane's own byte ceilings, shown at their defaults;\n"
        "# uncomment to override. Widen `chunk_max_bytes` for a backfill window — a\n"
        "# `blizzard runner transcript reship` spends that budget a SECOND time over the\n"
        "# same chunk — then restore it. Keep `record_max_bytes` at or BELOW the hub's own\n"
        "# `record_max_bytes`: over the hub's, a record loses its turns whole; over this\n"
        "# one, the pump merely shrinks them.\n" + caps
    )


def _toml_origins(origins: tuple[str, ...]) -> str:
    return json.dumps(origins[0]) if len(origins) == 1 else json.dumps(list(origins))


def _toml_scalar(value: object) -> str:
    return json.dumps(value) if not isinstance(value, bool) else str(value).lower()


def _subscription_toml(subscription: SubscriptionDeclaration) -> str:
    return (
        "\n[[subscription]]\n"
        f"slug = {json.dumps(subscription.slug)}\n"
        f"name = {json.dumps(subscription.name)}\n"
        f"provider = {json.dumps(subscription.provider)}\n"
        f"sample_interval_seconds = {subscription.sample_interval_seconds}\n"
        + (
            f"credentials_path = {json.dumps(subscription.credentials_path)}\n"
            if subscription.credentials_path is not None
            else ""
        )
    )


def _workspace_toml(workspace: HostWorkspace) -> str:
    return (
        "\n[[workspace]]\n"
        f"id = {json.dumps(workspace.id)}\n"
        f"root = {json.dumps(workspace.root)}\n"
        f"provider = {json.dumps(workspace.provider)}\n"
        f"envs = {json.dumps(list(workspace.envs))}\n"
        f"max_environments = {workspace.max_environments}\n"
        f"base_branch = {json.dumps(workspace.base_branch)}\n"
        f"prompt = {json.dumps(workspace.prompt)}\n"
        f"prompt_file = {json.dumps(workspace.prompt_file)}\n"
        f"prompt_package = {json.dumps(workspace.prompt_package)}\n"
        + "".join(
            f"\n[[workspace.repo]]\nname = {json.dumps(repo.name)}\nurl = {json.dumps(repo.url)}\n"
            for repo in workspace.repos
        )
    )


_TABLE_HEADER = re.compile(r"^\[([^\[\]]+)\]$", re.MULTILINE)


def _runner_toml(entry: RunnerEntry) -> str:
    own = entry.own
    projects = ", ".join(
        f"{{ project = {json.dumps(b.project)}, workspace = {json.dumps(b.workspace)} }}" for b in entry.bindings
    )
    text = "\n[[runner]]\n"
    if entry.id is not None:
        text += f"id = {json.dumps(entry.id)}\n"
    text += f"name = {json.dumps(entry.name)}\n"
    if entry.subscription is not None:
        text += f"subscription = {json.dumps(entry.subscription)}\n"
    if own is not None and own.agents:
        text += f"agents = {own.agents}\n"
    text += f"projects = [{projects}]\n"
    if own is None:
        return text
    text += f"gates = {json.dumps(list(own.gates))}\n"
    text += f"runner_prompt = {json.dumps(own.runner_prompt)}\n"
    text += f"runner_prompt_file = {json.dumps(own.runner_prompt_file)}\n"
    text += f"transcripts_root = {json.dumps(own.transcripts_root)}\n"
    text += "".join(section.root_toml() for section in own.harness_sections)
    text += f"\n[runner.harness]\nautonomy = {json.dumps(str(own.autonomy))}\n"
    if own.harness_config_dir:
        text += f"config_dir = {json.dumps(str(own.harness_config_dir))}\n"
    text += _under_runner("".join(section.table_toml() for section in own.harness_sections))
    text += (
        "\n[runner.worker]\n"
        f"env_passthrough = {json.dumps(list(own.worker_env_passthrough))}\n"
        f"path_prepend = {json.dumps(list(own.worker_path_prepend))}\n"
    )
    if own.queue_strict:
        text += "\n[runner.queue]\nstrict = true\n"
    spend = [
        f"{key} = {value}\n"
        for key, value in (("chunk_cap_usd", own.chunk_cap_usd), ("runner_ceiling_usd", own.runner_ceiling_usd))
        if value is not None
    ]
    if spend:
        text += "\n[runner.cost]\n" + "".join(spend) + f"window_hours = {own.runner_ceiling_window_hours}\n"
    text += _throttle_toml(own.throttle)
    if own.context_warn_tokens is not None:
        text += (
            "\n[runner.context]\n"
            f"warn_tokens = {own.context_warn_tokens}\n"
            f"sample_interval_seconds = {own.context_sample_interval_seconds}\n"
        )
    return text


def _throttle_toml(settings: ThrottleSettings | None) -> str:
    """``[runner.throttle]``, each hold-or-release choice that is not ``hold``, and each configured lever's sub-table,
    and only those; loading the text gives back ``settings``."""
    if settings is None:
        return ""
    text = "\n[runner.throttle]\n"
    for key, choice in (("on_decline", settings.on_decline), ("on_usage_limit", settings.on_usage_limit)):
        if choice is not HoldOrRelease.HOLD:
            text += f"{key} = {json.dumps(choice.value)}\n"
    if (pace := settings.pace) is not None:
        text += "\n[runner.throttle.pace]\n"
        if pace.window is not None:
            text += f"window = {json.dumps(pace.window)}\n"
        text += f"lead_pct = {json.dumps(pace.lead_pct)}\n"
        for step in pace.taper:
            text += (
                "\n[[runner.throttle.pace.taper]]\n"
                f"from_pct = {json.dumps(step.from_pct)}\nlead_pct = {json.dumps(step.lead_pct)}\n"
            )
    if (utilization := settings.utilization) is not None:
        text += f"\n[runner.throttle.utilization]\nbelow_pct = {json.dumps(utilization.below_pct)}\n"
        if utilization.windows is not None:
            text += f"windows = {json.dumps(list(utilization.windows))}\n"
    if (budget := settings.budget) is not None:
        text += (
            f"\n[runner.throttle.budget]\nusd = {json.dumps(budget.usd)}\n"
            f"window_hours = {json.dumps(budget.window_hours)}\n"
        )
    if (schedule := settings.schedule) is not None:
        days = [DAY_NAMES[day] for day in schedule.days]
        spans = [f"{_clock(span.start_minute)}-{_clock(span.end_minute)}" for span in schedule.spans]
        text += f"\n[runner.throttle.schedule]\ndays = {json.dumps(days)}\n"
        if spans:
            text += f"hours = {json.dumps(spans)}\n"
    return text


def _clock(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _under_runner(tables: str) -> str:
    """The harness sections' file-level tables, rehomed beneath ``[[runner]]``."""
    return _TABLE_HEADER.sub(r"[runner.\1]", tables)
