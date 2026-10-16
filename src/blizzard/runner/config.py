"""Runner runtime configuration — resolved from a runtime directory.

The store URL is the single portability knob (``bzh:sql-portable``), defaulting to embedded sqlite."""

from __future__ import annotations

import base64
import binascii
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from blizzard.foundation.public_origins import PublicOrigins
from blizzard.foundation.roles import domain_model
from blizzard.foundation.runner_paths import runner_prefix
from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.foundation.trace_export.config import TracingConfig
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.runner.auth.roles import RolePolicy
from blizzard.runner.auth.session import CALLBACK_PATH
from blizzard.runner.config_table import ConfigError, Table
from blizzard.runner.environments.factory import WorkspaceSettings
from blizzard.runner.environments.provider import WorkspaceRepo
from blizzard.runner.environments.workspaces import home_workspace_id
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.bundle import HARNESS_CONFIG_DIRNAME
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.wiring import HarnessSections, HarnessSettings
from blizzard.runner.harness.workspace_prompts import PACKAGED, UnknownWorkspacePromptSample
from blizzard.runner.host_layout import HostLayout

#: Winter's own exporter variables; only the runner sets them, and only under `worker_programs`.
_WINTER_OTEL_PREFIX = "WINTER_OTEL_"

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
#: What a freshly scaffolded runner serves: the project every tenant holds from its creation.
SCAFFOLD_PROJECTS: tuple[str, ...] = ("default",)
#: The agents each scaffolded binding runs at once.
SCAFFOLD_AGENTS = 1
ENV_WORKSPACE_PROMPT = "BZ_WORKSPACE_PROMPT"  # the runner-owned workspace prompt, inline
ENV_WORKSPACE_PROMPT_PACKAGE = "BZ_WORKSPACE_PROMPT_PACKAGE"  # a packaged workspace-prompt sample, by name (#344)
ENV_RUNNER_PROMPT = "BZ_RUNNER_PROMPT"  # the blizzard-preamble override, inline
# Where the harness writes session transcripts; empty resolves to a default at
# the composition root, never here.
ENV_TRANSCRIPTS_ROOT = "BZ_TRANSCRIPTS_ROOT"
ENV_ENV_PASSTHROUGH = "BZ_RUNNER_ENV_PASSTHROUGH"  # comma-separated names seeded into [worker] env_passthrough
# The browser-reachable base URLs this runner answers on, comma-separated.
ENV_PUBLIC_URL = "BZ_RUNNER_PUBLIC_URL"

# Reconciliation-loop defaults.
DEFAULT_HUB_URL = "http://127.0.0.1:8421"  # the hub's default bind (band +2)
DEFAULT_RUNNER_NAME = "runner-local"
DEFAULT_WORKSPACE_ID = "workspace-local"
DEFAULT_BASE_BRANCH = "main"
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

    Config rather than graph content, so the line is re-aimed without re-minting graphs."""

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
class SubscriptionDeclaration:
    """One declared provider subscription — the runner-unique, immutable
    join key everything downstream keys on is ``slug``; ``name`` is operator-facing only.

    The host declares them; a runner names the one it spends (:attr:`RunnerConfig.subscriptions`)."""

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
        offending entry. An empty list (none authored) is zero declarations; anything else
        non-list (e.g. a singular ``[subscription]`` table) is rejected rather than silently read as zero."""
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
        """Whether transcript segments are shipped to the hub; off by default."""
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
class HostWorkspace:
    """One ``[[workspace]]``: a root and the environments it names within it, and its standing prompt — three source
    knobs, one effective value (:meth:`resolved_prompt`)."""

    id: str
    root: str = ""
    provider: str = "winter"
    envs: tuple[str, ...] = DEFAULT_ENV_POOL
    max_environments: int = DEFAULT_MAX_ENVIRONMENTS
    repos: tuple[WorkspaceRepo, ...] = ()
    base_branch: str = DEFAULT_BASE_BRANCH
    prompt: str = ""
    prompt_file: str = ""
    #: A packaged workspace-prompt sample, by name; exclusive with the two above.
    prompt_package: str = ""

    def settings(self, host_root: Path) -> WorkspaceSettings:
        """The provider's inputs, with relative roots resolved against ``host_root``."""
        path = Path(self.root) if self.root else host_root / "workspace"
        return WorkspaceSettings(
            provider=self.provider,
            root=host_root,
            workspace_root=self.root,
            effective_workspace_root=str((path if path.is_absolute() else host_root / path).resolve()),
            repos=self.repos,
            max_environments=self.max_environments,
            base_branch=self.base_branch,
            env_pool=self.envs,
        )

    def resolved_prompt(self, host_root: Path) -> str:
        """The effective static workspace prompt, resolved from its three knobs.

        ``prompt_package`` names a packaged sample and is exclusive with the other two, which keep their
        file-wins-over-inline precedence; a configured-but-missing sample or file raises, while all three empty is
        valid and returns ``""``. A relative ``prompt_file`` resolves against ``host_root``."""
        if self.prompt_package:
            self._sole_prompt_knob()
            try:
                return PACKAGED.text(self.prompt_package)
            except UnknownWorkspacePromptSample as exc:
                packaged = ", ".join(PACKAGED.names) or "none"
                raise ConfigError(
                    f"workspace {self.id}: prompt_package names no packaged sample: "
                    f"{self.prompt_package} (packaged: {packaged})"
                ) from exc
        if self.prompt_file:
            path = Path(self.prompt_file)
            if not path.is_absolute():
                path = host_root / path
            if not path.exists():
                raise ConfigError(f"workspace {self.id}: prompt_file does not exist: {path}")
            return path.read_text()
        return self.prompt

    def prompt_source(self) -> str:
        """Which knob :meth:`resolved_prompt` resolves from, in its precedence."""
        if self.prompt_package:
            return f'package "{self.prompt_package}"'
        if self.prompt_file:
            return f"file {self.prompt_file}"
        return "inline prompt" if self.prompt else "none"

    def _sole_prompt_knob(self) -> None:
        """Reject a named sample set alongside either text knob — the pair has no precedence rule."""
        conflicts = [name for name, value in (("prompt_file", self.prompt_file), ("prompt", self.prompt)) if value]
        if conflicts:
            raise ConfigError(
                f"workspace {self.id}: prompt_package is exclusive with {', '.join(conflicts)} — set one of them"
            )


def bound_workspaces(
    declared: tuple[HostWorkspace, ...], bindings: tuple[ProjectBinding, ...] | None
) -> tuple[HostWorkspace, ...]:
    """Each of ``declared`` that ``bindings`` name, in binding order; ``declared``'s first when they name none."""
    by_id = {workspace.id: workspace for workspace in declared}
    bound = dict.fromkeys(binding.workspace for binding in bindings or () if binding.workspace in by_id)
    return tuple(by_id[workspace_id] for workspace_id in bound) or declared[:1]


def bound_workspace_ids(bindings: tuple[ProjectBinding, ...] | None) -> tuple[str, ...]:
    """The workspace each of ``bindings`` names, in binding order, declared or not."""
    return tuple(dict.fromkeys(binding.workspace for binding in bindings or ()))


@domain_model
@dataclass(frozen=True)
class ProjectBinding:
    """One ``[[runner]] projects`` binding: a project, the workspace it runs in, and its agent count."""

    project: str
    workspace: str
    agents: int


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
    #: This runner's display name at the hub — not unique, and never its identity.
    name: str = DEFAULT_RUNNER_NAME
    #: The hub-minted id the host file's ``[[runner]]`` names; the runner's store binds to it before anything runs.
    runner_id: str | None = None
    #: The resolved hub bearer token (see :attr:`token_env` for its variable); empty is a valid state.
    hub_token: str = field(default="", repr=False)
    #: Names the env var carrying the session-signing secret; :attr:`session_secret` is the
    #: resolved bytes, empty when the variable is unset.
    session_secret_env: str = DEFAULT_SESSION_SECRET_ENV
    session_secret: bytes = field(default=b"", repr=False)
    #: Every workspace the host declares, in file order; a config built without a host file holds the default one.
    workspaces: tuple[HostWorkspace, ...] = (HostWorkspace(DEFAULT_WORKSPACE_ID),)
    #: `[harness] autonomy`, the runner-wide approval posture each harness binding translates.
    autonomy: Autonomy = Autonomy.Dangerous
    #: Where :attr:`autonomy`'s posture comes from: a binding's legacy override, `[harness] autonomy`, or `"default"`.
    autonomy_source: str = "default"
    #: `[harness] config_dir`, the operator-owned harness-config bundle; `None` is no bundle.
    harness_config_dir: Path | None = None
    #: Every harness binding's own config section, in catalog order.
    harness_sections: HarnessSections = field(default_factory=HarnessSections.defaults)
    #: Node NAMES this runner imposes a human gate on; reloaded every tick.
    gates: tuple[str, ...] = ()
    #: The projects this runner serves, by id or slug, sent at every registration. ``None`` — no
    #: ``projects`` key — declares nothing, so the hub keeps the declaration it holds; ``()`` — an
    #: explicit ``projects = []`` — serves none.
    projects: tuple[str, ...] | None = None
    #: The override of the baked-in blizzard preamble, prepended ahead of the
    #: workspace prompt; empty resolves to the baked default.
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
    #: The declared subscription this runner names — none or one — which it samples and reports.
    subscriptions: tuple[SubscriptionDeclaration, ...] = ()
    #: The projects this runner serves, each bound to a workspace of its host with its agent count, in file order;
    #: sent at every registration beside :attr:`projects`. ``None`` — a config built without a host file — sends none.
    bindings: tuple[ProjectBinding, ...] | None = None
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
        credentials."""
        if not TracingSettings.of(os.environ).enabled():
            return ()
        return tuple(name for name in self.worker_env_passthrough if name.startswith(("OTEL_", _WINTER_OTEL_PREFIX)))

    @property
    def missing_worker_path_prepend_entries(self) -> tuple[str, ...]:  # ast-grep-ignore: bzh:property-delegates
        """Every configured ``[worker] path_prepend`` entry absent on disk right now; a missing
        entry never contributes to a spawned child's ``PATH``."""
        return tuple(entry for entry in self.worker_path_prepend if not Path(entry).exists())

    @property
    def public_origins(self) -> PublicOrigins:
        """Every origin this runner answers on, in declaration order."""
        return PublicOrigins.of(*self.public_urls)

    @property
    def public_url(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """The canonical origin — the first declared; empty when none is declared."""
        return self.public_origins.canonical or ""

    @property
    def role_policy(self) -> RolePolicy:
        """The ``[auth]`` role precedence: superuser, per-user roles, then the hub-role default."""
        return RolePolicy(
            superuser=self.auth_superuser, users=self.auth_users, hub_role_default=self.auth_hub_role_default
        )

    @property
    def redirect_uris(self) -> tuple[str, ...]:
        """The redirect URIs this runner presents to the hub's IdP authorize endpoint — one
        per declared origin, derived from :attr:`public_origins`, never independently configured."""
        return self.public_origins.callback_uris(CALLBACK_PATH)

    @property
    def token_env(self) -> str:
        """The variable carrying this runner's hub token, derived from its name."""
        return hub_token_variable(self.name)

    @property
    def data_dir(self) -> Path:
        return HostLayout(self.root).data_dir

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
    def bound_workspaces(self) -> tuple[HostWorkspace, ...]:
        """Each declared workspace this runner's bindings name, in binding order; the host's first when they name
        none."""
        return bound_workspaces(self.workspaces, self.bindings)

    @property
    def home_workspace(self) -> HostWorkspace:
        """The workspace this runner is reported and addressed by as such (:func:`home_workspace_id`)."""
        by_id = {workspace.id: workspace for workspace in self.workspaces}
        return by_id[home_workspace_id(self.bound_workspace_ids, tuple(by_id))]

    @property
    def bound_workspace_ids(self) -> tuple[str, ...]:
        """The workspace each of this runner's bindings names, in binding order, declared or not."""
        return bound_workspace_ids(self.bindings)

    @property
    def socket_path(self) -> Path:
        """The local API's unix socket in the host directory; filesystem permissions are its access control."""
        return HostLayout(self.root).socket_path

    @property
    def local_api_url(self) -> str:
        """The runner's own base on its host's TCP door, ``http://<host>:<port>/runners/<runner_id>`` — the one
        derivation of the ``BLIZZARD_RUNNER_URL`` a worker or taken-over session is handed, shared by the loop
        context and the takeover service so the two cannot drift."""
        return f"http://{self.host}:{self.port}{runner_prefix(self.served_id())}"

    def served_id(self) -> str:
        """The id a host serves this runner under: its hub-minted id, or — for a runner not yet joined, which only an
        offline build holds — its name."""
        return self.runner_id or self.name

    def resolved_runner_prompt(self) -> str:
        """The effective override for the blizzard preamble, from its two knobs.

        Mirrors :meth:`HostWorkspace.resolved_prompt`: the file knob wins when set, and a
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
        """The subscriptions this runner names — what every reader samples, reports and lists. A runner naming none
        declares, samples and reports none."""
        return self.subscriptions

    def auth_headers(self) -> dict[str, str]:
        """The outbound ``Authorization`` header every runner->hub call carries.

        One credential path for every outbound call rather than a header built per call
        site. Empty when :attr:`hub_token` is unset."""
        if not self.hub_token:
            return {}
        return {"Authorization": f"Bearer {self.hub_token}"}
