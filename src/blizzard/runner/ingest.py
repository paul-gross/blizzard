"""``blizzard runner ingest`` — move one runner from another store into this host: its rows, configuration and token.

The source — a host directory or a reset archive — is read only through a migrated backup copy until its rows go.
The copy, each configuration edit, and the removal touch separate files, so each edit is skipped when made already and a
re-run resumes from the ``host_ingests`` record rather than repeating. The order and windows are recorded in
``bzh:crash-exemptions-runner``."""

from __future__ import annotations

import json
import shutil
import tempfile
import tomllib
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import collaborator, domain_model
from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.foundation.store.migrations import MigrationConnectionError, MigrationRefused, MigrationRunner
from blizzard.runner.config import HostWorkspace, ProjectBinding, SubscriptionDeclaration
from blizzard.runner.config_table import ConfigError
from blizzard.runner.environments.repository import EnvBinding
from blizzard.runner.host_config import (
    RUNNER_PATH_KEYS,
    HostConfig,
    RunnerEntry,
    subscription_toml,
    workspace_toml,
)
from blizzard.runner.host_layout import (
    HOST_CONFIG_FILENAME,
    RUNNERS_DIRNAME,
    Graduation,
    HostHeld,
    HostLayout,
    HostLock,
    runner_table,
    with_runner_values,
)
from blizzard.runner.hub.client import HostRecord, HostRefused, HubClientError, IHostHubClient
from blizzard.runner.hub.host_identity import HostIdentity
from blizzard.runner.hub.host_registration import standing_of
from blizzard.runner.hub.identity import HostStanding, RunnerIdentity, RunnerIdentityHolder
from blizzard.runner.hub.runner_transfer import HostIngest, WorkspacePromptConflict
from blizzard.runner.runtime import (
    IRunnerRowMoves,
    StoreResetRefused,
    backup_sqlite,
    set_aside_sqlite,
    sqlite_store_file,
    sqlite_url,
)
from blizzard.runner.store import MIGRATIONS_DIR

__all__ = [
    "INGEST_COMMAND",
    "HeldBinding",
    "IHostFileEditor",
    "IIngestStores",
    "ITokenFiles",
    "IngestOutcome",
    "IngestRefused",
    "IngestRequest",
    "RunnerIngest",
    "RunnerStoreView",
    "SourceKind",
    "source_kind",
]

INGEST_COMMAND = "blizzard runner ingest"

_CP_AFTER_COPY = crashpoint(
    "ingest.after-copy.before-config",
    "the runner's rows and its ingest record are in this host's store; no configuration edit made yet",
)
_CP_AFTER_CONFIG = crashpoint(
    "ingest.after-config.before-removal",
    "this host holds the runner's configuration and token and the source's token is gone; its rows are still there",
)

#: The name every source copy's directory in this host's data directory starts with.
_COPY_PREFIX = "ingest-copy-"

#: The first bytes of every SQLite database file.
_SQLITE_HEADER = b"SQLite format 3\x00"

_log = get_logger("blizzard.runner.ingest")


class IngestRefused(Exception):
    """The ingest cannot go ahead; the message names why and what clears it. Nothing was changed unless it says so."""


class SourceRunning(IngestRefused):
    """The source, or this host, is held by a running process."""


class SourceUnsupported(IngestRefused):
    """The source is not a store ingest reads."""


class HostNotHolding(IngestRefused):
    """This host is not registered, or its hub does not hold the runner under it, or holds it in another tenant."""


class RunnerBusy(IngestRefused):
    """The runner holds an open elicitation or takeover in the source."""


class RunnerAlreadyHere(IngestRefused):
    """This host already holds a runner of that name or id, not from an ingest of this source."""


class WorkspaceConflict(IngestRefused):
    """The runner's workspace cannot stand beside this host's under the id it would take."""


class BindingConflict(IngestRefused):
    """A held binding of the runner names an environment a runner of this host holds."""


class TokenConflict(IngestRefused):
    """The runner's token cannot move: the source holds none, or this host's ``.env`` holds a different one."""


class SourceMoved(IngestRefused):
    """The source has run since the copy this host took of it."""


class SourceKind(StrEnum):
    """What an ingest source is."""

    HOST_DIRECTORY = "host directory"
    ARCHIVE = "archive store"
    UNGRADUATED = "runner directory that has not graduated"


def source_kind(source: Path) -> SourceKind:
    """What ``source`` is: a host directory, a SQLite archive store, or a runner directory that has not graduated.
    Raises :class:`SourceUnsupported` for anything else."""
    if source.is_dir():
        graduation = HostLayout(source).graduation()
        if graduation is Graduation.GRADUATED:
            return SourceKind.HOST_DIRECTORY
        if graduation is Graduation.UNGRADUATED:
            return SourceKind.UNGRADUATED
        raise SourceUnsupported(f"{source} holds no {HOST_CONFIG_FILENAME}, so it is not a host directory")
    if source.is_file():
        with source.open("rb") as handle:
            if handle.read(len(_SQLITE_HEADER)) == _SQLITE_HEADER:
                return SourceKind.ARCHIVE
        raise SourceUnsupported(f"{source} is not a SQLite store, so it is not an archive `runner store reset` wrote")
    raise SourceUnsupported(f"{source} does not exist")


@domain_model
@dataclass(frozen=True)
class IngestRequest:
    """What the operator asked for: the ``source``, the ``runner`` by name or id, and the new ``workspace_id`` its
    workspace takes here, if any."""

    source: Path
    runner: str
    workspace_id: str | None = None


@domain_model
@dataclass(frozen=True)
class IngestOutcome:
    """What an ingest did: the runner, whether it resumed an interrupted ingest, and the source files it moved aside."""

    runner_id: str
    runner_name: str
    resumed: bool
    set_aside: tuple[Path, ...] = ()


@domain_model
@dataclass(frozen=True)
class HeldBinding:
    """A held binding, beside the runner holding it."""

    runner_id: str
    binding: EnvBinding


@domain_model
@dataclass(frozen=True)
class RunnerStoreView:
    """What an ingest reads of one store about one runner: the runners its identity rows name, the runner's identity,
    its open elicitations and takeovers, its held bindings, and the store's last liveness stamp."""

    named_runners: tuple[str, ...]
    identity: RunnerIdentity | None
    open_elicitation_lease_ids: frozenset[str]
    open_takeover_chunk_ids: frozenset[str]
    held_bindings: tuple[EnvBinding, ...]
    last_alive_at: datetime | None


class IIngestStores(IRunnerRowMoves, Protocol):
    """The stores an ingest reads and writes, each named by its URL."""

    def runner_view(self, url: str, runner_id: str) -> RunnerStoreView:
        """What the store at ``url`` holds about ``runner_id``."""
        ...

    def named_runners(self, url: str) -> tuple[str, ...]:
        """The runners the identity rows of the store at ``url`` name."""
        ...

    def host_identity(self, url: str) -> HostIdentity | None:
        """The host identity the store at ``url`` recorded, if any."""
        ...

    def held_bindings(self, url: str) -> tuple[HeldBinding, ...]:
        """Every held binding of every runner the store at ``url`` names."""
        ...

    def latest_host_ingest(self, url: str, runner_id: str) -> HostIngest | None:
        """The newest ingest of ``runner_id`` the store at ``url`` recorded."""
        ...

    def record_host_identity(self, url: str, identity: HostIdentity) -> None:
        """Record ``identity`` as the host identity of the store at ``url``."""
        ...


class IHostFileEditor(Protocol):
    """The host-file appends an ingest makes."""

    def append_workspace(self, layout: HostLayout, table: str) -> None: ...
    def append_subscription(self, layout: HostLayout, table: str) -> None: ...
    def append_runner(self, layout: HostLayout, table: str) -> None: ...


class ITokenFiles(Protocol):
    """A runner token in a host directory's ``.env``."""

    def held(self, root: Path, token_env: str) -> str:
        """The token ``.env`` assigns ``token_env``, or ``""``."""
        ...

    def write(self, root: Path, token_env: str, token: str) -> None: ...
    def remove(self, root: Path, token_env: str) -> None: ...


@domain_model
@dataclass(frozen=True)
class _Source:
    """The resolved source: its kind, the path the ingest record names, its store file, and — for a host directory —
    its configuration."""

    kind: SourceKind
    path: Path
    store: Path
    config: HostConfig | None


@domain_model
@dataclass(frozen=True)
class _ConfigPlan:
    """The configuration edits an ingest makes, each skipped when already made."""

    workspaces: tuple[HostWorkspace, ...] = ()
    workspace_ids: Mapping[str, str] | None = None
    subscription: SubscriptionDeclaration | None = None
    runner_table: str | None = None
    files: tuple[tuple[Path, Path], ...] = ()
    token: str = ""


@collaborator
@dataclass(frozen=True)
class _SourceRunner:
    """The moving runner as :func:`standing_of` judges it: its id and the identity the source recorded."""

    runner_id: str
    identity: RunnerIdentityHolder

    @property
    def identity_verdict(self) -> _NoVerdict:
        return _NoVerdict()


class _NoVerdict:
    def stand(self, standing: HostStanding, *, reason: str | None = None) -> None:
        del standing, reason


@dataclass(frozen=True)
class RunnerIngest:
    """Ingests runners into the host ``host``."""

    host: HostConfig
    stores: IIngestStores
    host_file: IHostFileEditor
    tokens: ITokenFiles
    hub: IHostHubClient
    #: What answers on a socket path, if anything.
    holder: Callable[[Path], str | None]
    clock: IClock

    def run(self, request: IngestRequest) -> IngestOutcome:
        """Ingest the runner ``request`` names, or complete an ingest of it interrupted part-way. Raises an
        :class:`IngestRefused` naming why it cannot."""
        kind = source_kind(request.source)
        if kind is SourceKind.UNGRADUATED:
            raise SourceUnsupported(
                f"{request.source} is a runner directory that has not graduated to a host; ingest reads a host "
                "directory or an archive store — graduate it first (docs/upgrade.md), then ingest from it"
            )
        path = request.source.resolve()
        if path == self.host.root:
            raise SourceUnsupported(f"{path} is this host; name another host directory or an archive store")
        with (
            self._held(self.host.layout, "this host"),
            self._held(HostLayout(path), "the source") if kind is SourceKind.HOST_DIRECTORY else nullcontext(),
        ):
            source = self._source(kind, path)
            return self._ingest(source, request)

    @contextmanager
    def _held(self, layout: HostLayout, which: str) -> Iterator[None]:
        """``layout``'s directory held for the run: its lock taken, and nothing answering on its sockets."""
        try:
            lock = HostLock.acquire(layout)
        except HostHeld as exc:
            raise SourceRunning(f"{which} is running ({exc}); stop it, then re-run") from exc
        with lock:
            for socket in (layout.socket_path, layout.legacy_socket_path):
                if (held := self.holder(socket)) is not None:
                    raise SourceRunning(f"{which} is running ({held}); stop it, then re-run")
            yield

    def _source(self, kind: SourceKind, path: Path) -> _Source:
        if kind is SourceKind.ARCHIVE:
            return _Source(kind, path, path, None)
        try:
            config = HostConfig.load(path)
            store = sqlite_store_file(config.db_url)
        except (ConfigError, StoreResetRefused) as exc:
            raise SourceUnsupported(f"cannot read the source host at {path}: {exc}") from exc
        return _Source(kind, path, store, config)

    def _entry(self, source: _Source, runner: str) -> RunnerEntry:
        """The moving runner's ``[[runner]]``: the source's, or — for an archive, which carries rows only — this
        host's."""
        config = source.config or self.host
        try:
            entry = config.sole_runner(runner)
        except ConfigError as exc:
            if source.config is None:
                raise SourceUnsupported(
                    f"an archive store carries the runner's rows only, so its [[runner]] and token must already be in "
                    f"this host: {exc}"
                ) from exc
            raise SourceUnsupported(str(exc)) from exc
        if entry.id is None:
            raise SourceUnsupported(f"runner {entry.name} has no id in {config.config_path}, so the hub holds none")
        return entry

    def _ingest(self, source: _Source, request: IngestRequest) -> IngestOutcome:
        entry = self._entry(source, request.runner)
        runner_id = entry.id
        assert runner_id is not None
        here = self.host.db_url
        present = runner_id in self.stores.named_runners(here)
        record = self.stores.latest_host_ingest(here, runner_id)
        resuming = present and record is not None and record.source == str(source.path)
        if present and not resuming:
            raise RunnerAlreadyHere(
                f"this host's store already holds runner {entry.name} ({runner_id}), not from an ingest of "
                f"{source.path}; nothing was changed"
            )
        if not source.store.exists():
            if resuming:
                return IngestOutcome(runner_id, entry.name, resumed=True)
            raise SourceUnsupported(f"the source holds no store at {source.store}")
        with self._copy_of(source.store) as copy:
            view = self.stores.runner_view(copy, runner_id)
            if resuming and runner_id not in view.named_runners:
                # Removed from the source already: only the move-aside of an emptied source is left.
                return IngestOutcome(runner_id, entry.name, resumed=True, set_aside=self._set_aside_emptied(source))
            if resuming:
                assert record is not None
                if record.source_alive_at != view.last_alive_at:
                    raise SourceMoved(
                        f"the source {source.store} has run since this host {self.host.db_url} copied runner "
                        f"{entry.name} from it (its liveness stamp moved from {record.source_alive_at} to "
                        f"{view.last_alive_at}), so the copy may be stale; nothing was changed"
                    )
            else:
                self._refuse_fresh(source, entry, runner_id, view)
            plan = self._plan(source, entry, request.workspace_id)
            if not resuming:
                self._refuse_conflicts(entry, runner_id, view, plan)
                ingest = HostIngest(
                    runner_id=runner_id,
                    source=str(source.path),
                    source_alive_at=view.last_alive_at,
                    ingested_at=self.clock.now(),
                )
                try:
                    self.stores.copy_runner(copy, here, runner_id, workspace_ids=plan.workspace_ids, ingest=ingest)
                except WorkspacePromptConflict as exc:
                    raise WorkspaceConflict(f"{exc}; nothing was changed") from exc
                _log.info("runner rows ingested", runner_id=runner_id, source=str(source.path))
        _CP_AFTER_COPY.reached()
        self._configure(source, entry, plan)
        _CP_AFTER_CONFIG.reached()
        self.stores.remove_runner_rows(sqlite_url(source.store), runner_id, keep_counters=False)
        moved = self._set_aside_emptied(source)
        _log.info("runner ingested", runner_id=runner_id, source=str(source.path), resumed=resuming)
        return IngestOutcome(runner_id, entry.name, resumed=resuming, set_aside=moved)

    @contextmanager
    def _copy_of(self, store: Path) -> Iterator[str]:
        """A backup copy of the source ``store`` beside this host's store, migrated to head, removed on leaving."""
        data = self.host.layout.data_dir
        # This run holds the host's lock, so a copy left here is one a killed run never removed.
        for stale in data.glob(f"{_COPY_PREFIX}*"):
            shutil.rmtree(stale, ignore_errors=True)
        directory = Path(tempfile.mkdtemp(prefix=_COPY_PREFIX, dir=data))
        try:
            copy = directory / store.name
            backup_sqlite(store, copy)
            migrations = MigrationRunner(script_location=MIGRATIONS_DIR, url=sqlite_url(copy))
            current = migrations.current_revision()
            if current is not None and not migrations.knows_revision(current):
                raise SourceUnsupported(
                    f"the source store {store} is at revision {current}, which this build does not know: upgrade "
                    "blizzard here to a release at least as new as the source's"
                )
            try:
                migrations.upgrade("head")
            except (MigrationConnectionError, MigrationRefused) as exc:
                raise SourceUnsupported(f"the copy of the source store {store} cannot be migrated: {exc}") from exc
            yield sqlite_url(copy)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def _refuse_fresh(self, source: _Source, entry: RunnerEntry, runner_id: str, view: RunnerStoreView) -> None:
        """Every refusal a first ingest of the runner earns before anything is written."""
        record = self._hub_record()
        identity = self._host_identity(record)
        standing, reason = standing_of(_SourceRunner(runner_id, RunnerIdentityHolder(view.identity)), record, identity)
        if standing is HostStanding.NOT_HELD:
            raise HostNotHolding(
                f"the hub holds runner {entry.name} ({runner_id}) {reason}: take it under this host first with "
                f"`blizzard hub runner-host adopt {identity.host_id} {runner_id}`"
            )
        if standing is HostStanding.TENANT_DIFFERS:
            raise HostNotHolding(f"runner {entry.name} ({runner_id}) {reason}, so this host cannot take it in")
        if runner_id not in view.named_runners:
            raise SourceUnsupported(f"the source store {source.store} holds no runner {entry.name} ({runner_id})")
        if view.open_elicitation_lease_ids:
            raise RunnerBusy(
                f"runner {entry.name} holds an open elicitation on lease(s) "
                f"{', '.join(sorted(view.open_elicitation_lease_ids))}; let it finish in the source, then re-run"
            )
        if view.open_takeover_chunk_ids:
            raise RunnerBusy(
                f"runner {entry.name} holds an open takeover of chunk(s) "
                f"{', '.join(sorted(view.open_takeover_chunk_ids))}; end it in the source, then re-run"
            )
        if source.config is not None:
            for held in self.host.runners:
                if entry.name == held.name or (held.id is not None and held.id == runner_id):
                    raise RunnerAlreadyHere(
                        f"{self.host.config_path} already declares runner {held.name} ({held.id or 'no id'}), so it "
                        f"cannot take in runner {entry.name} ({runner_id}); nothing was changed"
                    )

    def _hub_record(self) -> HostRecord:
        hub = self.host.hub_url
        if not self.host.host_token.value:
            raise HostNotHolding(
                f"this host holds no host token, so it is not registered at its hub: register it with "
                f"`blizzard runner init {self.host.root}`, or add it with `blizzard hub runner-host add <name> "
                f"--hub-url {hub}`"
            )
        try:
            answer = self.hub.record()
        except HubClientError as exc:
            raise HostNotHolding(f"cannot read this host's record at the hub at {hub}: {exc}") from exc
        if isinstance(answer, HostRefused):
            raise HostNotHolding(f"the hub at {hub} refuses this host's record ({answer.status}: {answer.detail})")
        return answer

    def _host_identity(self, record: HostRecord) -> HostIdentity:
        """This host's recorded identity — recorded from ``record`` when it holds none yet — refused when it names
        another host or tenant than the hub answered."""
        held = self.stores.host_identity(self.host.db_url)
        if held is None:
            held = HostIdentity(host_id=record.host_id, tenant_id=record.tenant_id, registered_at=self.clock.now())
            self.stores.record_host_identity(self.host.db_url, held)
            return held
        if not held.same_host(record.host_id, record.tenant_id):
            raise HostNotHolding(
                f"this host is recorded as {held.host_id} in {held.tenant_id}, but its hub answers as "
                f"{record.host_id} in {record.tenant_id}"
            )
        return held

    def _plan(self, source: _Source, entry: RunnerEntry, workspace_id: str | None) -> _ConfigPlan:
        """The configuration edits the runner brings; an archive's are all made already."""
        if source.config is None:
            if workspace_id is not None:
                raise WorkspaceConflict(
                    "--workspace-id renames the workspace a host directory's runner brings; an archive's runner "
                    "already runs in this host's workspace"
                )
            return _ConfigPlan()
        config = source.config
        workspaces, ids = self._workspace_plan(config, entry, workspace_id)
        declared = {subscription.slug for subscription in self.host.subscriptions}
        subscription = (
            next((s for s in config.subscriptions if s.slug == entry.subscription), None)
            if entry.subscription is not None and entry.subscription not in declared
            else None
        )
        table, files = self._runner_plan(config, entry, ids)
        token = self.tokens.held(config.root, hub_token_variable(entry.name))
        return _ConfigPlan(
            workspaces=workspaces,
            workspace_ids=ids or None,
            subscription=subscription,
            runner_table=table,
            files=files,
            token=token,
        )

    def _workspace_plan(
        self, config: HostConfig, entry: RunnerEntry, workspace_id: str | None
    ) -> tuple[tuple[HostWorkspace, ...], dict[str, str]]:
        """The workspaces to append and the ids the runner's workspaces take here."""
        names = list(dict.fromkeys(binding.workspace for binding in entry.bindings))
        if workspace_id is not None and len(names) != 1:
            raise WorkspaceConflict(
                f"--workspace-id names one new id, but runner {entry.name} is bound to {len(names)} workspaces "
                f"({', '.join(names) or 'none'})"
            )
        theirs = {workspace.id: workspace for workspace in config.workspaces}
        ours = {workspace.id: workspace for workspace in self.host.workspaces}
        appended: list[HostWorkspace] = []
        ids: dict[str, str] = {}
        for name in names:
            offered = theirs.get(name)
            if offered is None:
                raise WorkspaceConflict(
                    f"runner {entry.name} binds workspace {name!r}, which the source does not declare"
                )
            shape = _shape(offered, config.root)
            target = workspace_id or name
            held = ours.get(target)
            if held is not None and _shape(held, self.host.root) != shape:
                if workspace_id is None:
                    raise WorkspaceConflict(
                        f"this host gives workspace id {target!r} to {_described(held, self.host.root)}, and the "
                        f"source's is {_described(offered, config.root)}: re-run with --workspace-id <new id>"
                    )
                raise WorkspaceConflict(
                    f"--workspace-id {target!r} is this host's workspace on {_described(held, self.host.root)}, not "
                    f"the source's {_described(offered, config.root)}; pick another id"
                )
            if held is None:
                twin = next((w for w in self.host.workspaces if _shape(w, self.host.root) == shape), None)
                if twin is not None:
                    raise WorkspaceConflict(
                        f"the source's workspace {name!r} is this host's workspace {twin.id!r} "
                        f"({_described(twin, self.host.root)}): re-run with --workspace-id {twin.id}"
                    )
                root, envs = shape
                for other in self.host.workspaces:
                    other_root, other_envs = _shape(other, self.host.root)
                    if other_root == root and other_envs & envs:
                        raise WorkspaceConflict(
                            f"the source's workspace {name!r} shares root {root} with this host's workspace "
                            f"{other.id!r}, and both name environment(s) {', '.join(sorted(other_envs & envs))}"
                        )
                appended.append(replace(offered, id=target, root=root))
            if target != name:
                ids[name] = target
        return tuple(appended), ids

    def _runner_plan(
        self, config: HostConfig, entry: RunnerEntry, ids: Mapping[str, str]
    ) -> tuple[str, tuple[tuple[Path, Path], ...]]:
        """The source's ``[[runner]]`` text with its bindings renamed and every path key pointing into the source
        rewritten to its copy under this host's ``runners/<name>/``, and the copies to make."""
        try:
            table = runner_table(config.layout, entry.name)
        except ConfigError as exc:
            raise SourceUnsupported(str(exc)) from exc
        values: dict[tuple[str, ...], str] = {}
        if ids:
            values[("projects",)] = _bindings_literal(
                tuple(ProjectBinding(b.project, ids.get(b.workspace, b.workspace), b.agents) for b in entry.bindings)
            )
        files: list[tuple[Path, Path]] = []
        runner_dir = self.host.root / RUNNERS_DIRNAME / entry.name
        raw = _parsed_table(table)
        for path_key in RUNNER_PATH_KEYS:
            value = _lookup(raw, path_key.key)
            if not isinstance(value, str) or not value:
                continue
            located = Path(value).expanduser()
            located = (located if located.is_absolute() else config.root / located).resolve()
            if not located.is_relative_to(config.root):
                continue
            inside = located.relative_to(config.root)
            if inside.parts[:2] == (RUNNERS_DIRNAME, entry.name):
                inside = Path(*inside.parts[2:])
            copied = runner_dir / inside
            files.append((located, copied))
            values[path_key.key] = _string_literal(str(copied))
        try:
            return with_runner_values(table, values), tuple(files)
        except ConfigError as exc:
            raise SourceUnsupported(f"runner {entry.name}'s [[runner]] cannot be carried: {exc}") from exc

    def _refuse_conflicts(self, entry: RunnerEntry, runner_id: str, view: RunnerStoreView, plan: _ConfigPlan) -> None:
        """The refusals that need the configuration plan: a held environment two runners would hold, and a token that
        cannot move."""
        renamed = plan.workspace_ids or {}
        ours = self.stores.held_bindings(self.host.db_url)
        for binding in view.held_bindings:
            workspace = renamed.get(binding.workspace_id, binding.workspace_id) if binding.workspace_id else None
            for held in ours:
                other = held.binding
                if other.environment_id != binding.environment_id or other.chunk_id == binding.chunk_id:
                    continue
                if workspace is None or other.workspace_id is None or other.workspace_id == workspace:
                    raise BindingConflict(
                        f"runner {entry.name} holds environment {binding.environment_id} of workspace "
                        f"{workspace or '(unknown)'} for chunk {binding.chunk_id}, and this host's runner "
                        f"{held.runner_id} holds it for chunk {other.chunk_id}; release one, then re-run"
                    )
        variable = hub_token_variable(entry.name)
        held_here = self.tokens.held(self.host.root, variable)
        if plan.runner_table is not None and not plan.token:
            raise TokenConflict(
                f"the source's .env holds no {variable} for runner {entry.name}, so there is none to move"
            )
        if plan.runner_table is None and not held_here:
            raise TokenConflict(
                f"an archive store carries no token, and this host's .env holds no {variable} for runner {entry.name}"
            )
        if plan.token and held_here and held_here != plan.token:
            raise TokenConflict(
                f"this host's .env already holds a different {variable}; remove it, then re-run (runner {runner_id})"
            )

    def _configure(self, source: _Source, entry: RunnerEntry, plan: _ConfigPlan) -> None:
        """Make each configuration edit not yet made, in the order a crash between any two leaves resumable."""
        layout = self.host.layout
        for workspace in plan.workspaces:
            if workspace.id not in _declared(layout, "workspace", "id"):
                self.host_file.append_workspace(layout, workspace_toml(workspace))
        if plan.subscription is not None and plan.subscription.slug not in _declared(layout, "subscription", "slug"):
            self.host_file.append_subscription(layout, subscription_toml(plan.subscription))
        if plan.runner_table is not None:
            for located, copied in plan.files:
                if located.is_dir():
                    shutil.copytree(located, copied, dirs_exist_ok=True)
                elif located.exists():
                    copied.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(located, copied)
            (self.host.root / RUNNERS_DIRNAME / entry.name).mkdir(parents=True, exist_ok=True)
            if entry.name not in _declared(layout, "runner", "name"):
                self.host_file.append_runner(layout, plan.runner_table)
        variable = hub_token_variable(entry.name)
        if plan.token and self.tokens.held(self.host.root, variable) != plan.token:
            self.tokens.write(self.host.root, variable, plan.token)
        if source.config is not None:
            self.tokens.remove(source.config.root, variable)

    def _set_aside_emptied(self, source: _Source) -> tuple[Path, ...]:
        """The source store moved aside whole when no runner is left in it."""
        if not source.store.exists() or self.stores.named_runners(sqlite_url(source.store)):
            return ()
        return set_aside_sqlite(source.store, now=self.clock.now()).moved


def _shape(workspace: HostWorkspace, root: Path) -> tuple[str, frozenset[str]]:
    """What makes a workspace the same one on two hosts: its root resolved against its own host, and its
    environments."""
    return workspace.settings(root).effective_workspace_root, frozenset(workspace.envs)


def _described(workspace: HostWorkspace, root: Path) -> str:
    resolved, envs = _shape(workspace, root)
    return f"root {resolved} with environments {', '.join(sorted(envs)) or 'none'}"


def _declared(layout: HostLayout, table: str, key: str) -> set[str]:
    """The ``key`` of every ``[[table]]`` the host file holds now."""
    entries = tomllib.loads(layout.config_path.read_text()).get(table, [])
    return {str(entry.get(key)) for entry in entries if isinstance(entry, dict)}


def _parsed_table(table: str) -> dict[str, object]:
    return tomllib.loads(table)["runner"][0]


def _lookup(raw: Mapping[str, object], key: tuple[str, ...]) -> object:
    node: object = raw
    for part in key:
        if not isinstance(node, Mapping):
            return None
        node = node.get(part)
    return node


def _string_literal(value: str) -> str:
    return json.dumps(value)


def _bindings_literal(bindings: tuple[ProjectBinding, ...]) -> str:
    entries = (
        f"{{ project = {_string_literal(b.project)}, workspace = {_string_literal(b.workspace)}, agents = {b.agents} }}"
        for b in bindings
    )
    return "[" + ", ".join(entries) + "]"
