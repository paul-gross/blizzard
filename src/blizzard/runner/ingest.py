"""``blizzard runner ingest`` — move one runner from another store into this host: its rows, configuration and token.

The source — a host directory, a reset archive, or a runner directory that has not graduated — is read only through a
migrated backup copy until its rows go. The copy, each configuration edit, and the removal touch separate files, so each
edit is skipped when made already and a re-run resumes from the ``host_ingests`` record. The order and windows are
recorded in ``bzh:crash-exemptions-runner``."""

from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.config_keys import ConfigView
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.foundation.store.migrations import MigrationConnectionError, MigrationRefused, MigrationRunner
from blizzard.runner.config import HostWorkspace, ProjectBinding, SubscriptionDeclaration
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_config import (
    GRADUATION_PROCEDURE,
    RUNNER_PATH_KEYS,
    UNSUFFIXED_TOKEN_VARIABLE,
    HostConfig,
    RunnerEntry,
    decode_host_text,
    read_legacy_document,
    subscription_toml,
    workspace_toml,
)
from blizzard.runner.host_file import runner_table, with_runner_values
from blizzard.runner.host_layout import (
    HOST_CONFIG_FILENAME,
    RUNNERS_DIRNAME,
    Graduation,
    HostLayout,
    hold_host,
)
from blizzard.runner.hub.client import HostRecord, HostRefused, HubClientError, IHostRecordReader
from blizzard.runner.hub.host_identity import HostIdentity, HostIdentityAlreadyRecorded, adopted
from blizzard.runner.hub.host_registration import standing_of
from blizzard.runner.hub.identity import HostStanding
from blizzard.runner.runtime import (
    StoreResetRefused,
    backup_sqlite,
    set_aside_sqlite,
    sqlite_store_file,
    sqlite_url,
)
from blizzard.runner.transfer.refusals import (
    HostNotHolding,
    SourceRunning,
    SourceUnsupported,
    WorkspaceConflict,
)
from blizzard.runner.transfer.rows import HostIngest, WorkspacePromptConflict
from blizzard.runner.transfer.rules import (
    DeclaredRunner,
    WorkspaceShape,
    place_workspaces,
    refuse_archive_rename,
    refuse_binding_conflict,
    refuse_busy,
    refuse_declared_here,
    refuse_source_moved,
    refuse_token_conflict,
    resumes,
)
from blizzard.runner.transfer.runner_stores import (
    HeldBinding,
    IRunnerBoundReadsFactory,
    IRunnerBoundStoresFactory,
    RunnerStoreView,
)

__all__ = [
    "INGEST_COMMAND",
    "IHostFileEditor",
    "IIngestStores",
    "ITokenFiles",
    "IngestOutcome",
    "IngestRequest",
    "RunnerIngest",
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

#: The revision from which every runner-owned table carries its runner, so one runner's rows can be removed from a
#: store that holds others.
_RUNNER_SCOPE_REVISION = "20261011_1000_runner_scope"

#: The first bytes of every SQLite database file.
_SQLITE_HEADER = b"SQLite format 3\x00"

_log = get_logger("blizzard.runner.ingest")


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


class IIngestStores(IRunnerBoundReadsFactory, IRunnerBoundStoresFactory, Protocol):
    """The stores an ingest reads and writes, each named by its URL: the store-wide reads here, and one runner's
    through the reads :meth:`reads_for` binds, or the stores :meth:`for_runner` binds to move its rows."""

    def named_runners(self, url: str) -> tuple[str, ...]:
        """The runners the identity rows of the store at ``url`` name."""
        ...

    def host_identity(self, url: str) -> HostIdentity | None:
        """The host identity the store at ``url`` recorded, if any."""
        ...

    def held_bindings(self, url: str) -> tuple[HeldBinding, ...]:
        """Every held binding of every runner the store at ``url`` names."""
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
    """The resolved source: its kind, the path the ingest record names, its store file, and — for a host directory,
    or a runner directory graduated in memory — its configuration, the text of its host file when that is not on disk,
    and the variable its ``.env`` holds a runner's token under when that is not the runner's own."""

    kind: SourceKind
    path: Path
    store: Path
    config: HostConfig | None
    host_text: str | None = None
    token_env: str | None = None

    def token_variable(self, runner_name: str) -> str:
        return self.token_env or hub_token_variable(runner_name)


@domain_model
@dataclass(frozen=True)
class _SourceCopy:
    """A migrated backup copy of a source store: its URL, and whether the source was at a revision that scopes every
    runner table by runner, which a runner-by-runner removal needs."""

    url: str
    runner_scoped: bool


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


@dataclass(frozen=True)
class RunnerIngest:
    """Ingests runners into the host ``host``."""

    host: HostConfig
    stores: IIngestStores
    host_file: IHostFileEditor
    tokens: ITokenFiles
    hub: IHostRecordReader
    #: What answers on a socket path, if anything.
    holder: Callable[[Path], str | None]
    clock: IClock
    #: The migrations of the runner store at a source copy's URL.
    migrations: Callable[[str], MigrationRunner]

    def run(self, request: IngestRequest) -> IngestOutcome:
        """Ingest the runner ``request`` names, or complete an ingest of it interrupted part-way. Raises an
        :class:`IngestRefused` naming why it cannot."""
        kind = source_kind(request.source)
        path = request.source.resolve()
        if path == self.host.root:
            raise SourceUnsupported(f"{path} is this host; name another host directory or an archive store")
        with (
            self._held(self.host.layout, "this host"),
            self._held(HostLayout(path), "the source") if kind is SourceKind.HOST_DIRECTORY else nullcontext(),
            self._answering(HostLayout(path).legacy_socket_path) if kind is SourceKind.UNGRADUATED else nullcontext(),
        ):
            source = self._source(kind, path)
            return self._ingest(source, request)

    @contextmanager
    def _held(self, layout: HostLayout, which: str) -> Iterator[None]:
        """``layout``'s directory held for the run: its lock taken, and nothing answering on its sockets."""
        with hold_host(
            layout,
            (layout.socket_path, layout.legacy_socket_path),
            self.holder,
            lambda held: SourceRunning(f"{which} is running ({held}); stop it, then re-run"),
        ):
            yield

    @contextmanager
    def _answering(self, socket: Path) -> Iterator[None]:
        """A runner directory that has not graduated, held for the run: a pre-host runner takes no lock, so only
        its ``runner.sock`` tells whether it runs."""
        if (held := self.holder(socket)) is not None:
            raise SourceRunning(f"the source is running ({held}); stop it, then re-run")
        yield

    def _source(self, kind: SourceKind, path: Path) -> _Source:
        if kind is SourceKind.ARCHIVE:
            return _Source(kind, path, path, None)
        if kind is SourceKind.UNGRADUATED:
            return self._ungraduated(path)
        try:
            config = HostConfig.load(path, flag_registry=self.host.flag_registry, strict=self.host.strict)
            store = sqlite_store_file(config.db_url)
        except (ConfigError, StoreResetRefused) as exc:
            raise SourceUnsupported(f"cannot read the source host at {path}: {exc}") from exc
        return _Source(kind, path, store, config)

    def _ungraduated(self, path: Path) -> _Source:
        """A runner directory that has not graduated, read as the host of one it graduates to: its store is
        ``data/runner.db``, its runner's id is the one that store records, and its token is under the variable its
        file names. Once its store is moved aside it carries nothing but its path, which an ingest record names."""
        layout = HostLayout(path)
        store = layout.legacy_store_path
        try:
            legacy = read_legacy_document(layout)
        except ConfigError as exc:
            raise SourceUnsupported(str(exc)) from exc
        named = legacy.get("db_url")
        if named is not None and sqlite_url(store) != named and f"sqlite:///{store}" != named:
            raise SourceUnsupported(
                f"{layout.legacy_config_path} names its store at {named}, not {store}: graduation reads the "
                f"directory's own store only, so graduate it by hand ({GRADUATION_PROCEDURE})"
            )
        token_env = str(legacy.get("token_env", UNSUFFIXED_TOKEN_VARIABLE))
        if not store.exists():
            return _Source(SourceKind.UNGRADUATED, path, store, None, token_env=token_env)
        with self._copy_of(store) as copy:
            named_runners = self.stores.named_runners(copy.url)
            if len(named_runners) != 1:
                raise SourceUnsupported(
                    f"the source store {store} records {len(named_runners)} runner identities "
                    f"({', '.join(named_runners) or 'none'}), not the one a runner before hosts holds"
                )
            [runner_id] = named_runners
            view = self.stores.reads_for(runner_id).runner_view(copy.url)
        try:
            config = HostConfig.graduated(
                path,
                legacy,
                runner_id=runner_id,
                served=view.served_projects,
                flag_registry=self.host.flag_registry,
                strict=self.host.strict,
            )
        except ConfigError as exc:
            raise SourceUnsupported(f"cannot graduate the runner file {layout.legacy_config_path}: {exc}") from exc
        return _Source(SourceKind.UNGRADUATED, path, store, config, host_text=config.to_toml(), token_env=token_env)

    def _entry(self, source: _Source, runner: str) -> RunnerEntry:
        """The moving runner's ``[[runner]]``: the source's, or — for an archive, which carries rows only — this
        host's."""
        config = source.config or self.host
        try:
            entry = config.sole_runner(runner)
        except ConfigError as exc:
            if source.kind is SourceKind.UNGRADUATED:
                raise SourceUnsupported(
                    f"the source holds no store at {source.store}, and this host holds no runner {runner} ingested "
                    f"from it: {exc}"
                ) from exc
            if source.config is None:
                raise SourceUnsupported(
                    f"an archive store carries the runner's rows only, so its [[runner]] and token must already be in "
                    f"this host: {exc}"
                ) from exc
            raise SourceUnsupported(str(exc)) from exc
        if entry.id is None:
            raise SourceUnsupported(f"runner {entry.name} has no id in {config.config_path}, so the hub holds none")
        if entry.errors:
            raise SourceUnsupported(f"runner {entry.name}'s [[runner]] is refused: {'; '.join(entry.errors)}")
        return entry

    def _ingest(self, source: _Source, request: IngestRequest) -> IngestOutcome:
        entry = self._entry(source, request.runner)
        runner_id = entry.id
        assert runner_id is not None
        here = self.host.db_url
        try:
            sqlite_store_file(here)
        except StoreResetRefused as exc:
            raise SourceUnsupported(f"this host's store cannot take an ingest: {exc}") from exc
        runner = self.stores.for_runner(runner_id)
        record = runner.latest_host_ingest(here)
        resuming = resumes(
            runner_id in self.stores.named_runners(here),
            record,
            source=str(source.path),
            runner_name=entry.name,
            runner_id=runner_id,
        )
        if not source.store.exists():
            if resuming:
                return IngestOutcome(runner_id, entry.name, resumed=True)
            raise SourceUnsupported(f"the source holds no store at {source.store}")
        with self._copy_of(source.store) as copy:
            view = runner.runner_view(copy.url)
            if resuming and runner_id not in view.named_runners:
                # Removed from the source already: only the move-aside of an emptied source is left.
                return IngestOutcome(runner_id, entry.name, resumed=True, set_aside=self._set_aside_emptied(source))
            if resuming:
                assert record is not None
                refuse_source_moved(
                    record, view.last_alive_at, runner_name=entry.name, source_store=str(source.store), here=here
                )
            else:
                self._refuse_fresh(source, entry, runner_id, view, copy)
            plan = self._plan(source, entry, request.workspace_id)
            if not resuming:
                self._refuse_conflicts(source, entry, runner_id, view, plan)
                ingest = HostIngest(
                    runner_id=runner_id,
                    source=str(source.path),
                    source_alive_at=view.last_alive_at,
                    ingested_at=self.clock.now(),
                )
                try:
                    runner.copy_runner(
                        copy.url,
                        here,
                        workspace_ids=plan.workspace_ids,
                        bound_workspaces=tuple(dict.fromkeys(binding.workspace for binding in entry.bindings)),
                        ingest=ingest,
                    )
                except WorkspacePromptConflict as exc:
                    raise WorkspaceConflict(f"{exc}; nothing was changed") from exc
                _log.info("runner rows ingested", runner_id=runner_id, source=str(source.path))
        _CP_AFTER_COPY.reached()
        self._configure(source, entry, plan)
        _CP_AFTER_CONFIG.reached()
        if source.kind is SourceKind.UNGRADUATED:
            # Its one runner is the whole store, which stays at the schema it ran: it is moved aside, not edited.
            moved = set_aside_sqlite(source.store, now=self.clock.now()).moved
        else:
            runner.remove_runner_rows(sqlite_url(source.store), keep_counters=False)
            moved = self._set_aside_emptied(source)
        _log.info("runner ingested", runner_id=runner_id, source=str(source.path), resumed=resuming)
        return IngestOutcome(runner_id, entry.name, resumed=resuming, set_aside=moved)

    @contextmanager
    def _copy_of(self, store: Path) -> Iterator[_SourceCopy]:
        """A backup copy of the source ``store`` beside this host's store, migrated to head, removed on leaving."""
        data = self.host.layout.data_dir
        # This run holds the host's lock, so a copy left here is one a killed run never removed.
        for stale in data.glob(f"{_COPY_PREFIX}*"):
            shutil.rmtree(stale, ignore_errors=True)
        directory = Path(tempfile.mkdtemp(prefix=_COPY_PREFIX, dir=data))
        try:
            copy = directory / store.name
            backup_sqlite(store, copy)
            migrations = self.migrations(sqlite_url(copy))
            current = migrations.current_revision()
            migrations.refuse_below_floor(store=str(store), current=current)
            if current is not None and not migrations.knows_revision(current):
                raise SourceUnsupported(
                    f"the source store {store} is at revision {current}, which this build does not know: upgrade "
                    "blizzard here to a release at least as new as the source's"
                )
            runner_scoped = migrations.reaches(current, _RUNNER_SCOPE_REVISION)
            try:
                migrations.upgrade("head")
            except (MigrationConnectionError, MigrationRefused) as exc:
                raise SourceUnsupported(f"the copy of the source store {store} cannot be migrated: {exc}") from exc
            yield _SourceCopy(sqlite_url(copy), runner_scoped)
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def _refuse_fresh(
        self, source: _Source, entry: RunnerEntry, runner_id: str, view: RunnerStoreView, copy: _SourceCopy
    ) -> None:
        """Every refusal a first ingest of the runner earns before anything is written."""
        record = self._hub_record()
        identity = self._host_identity(record)
        standing, reason = standing_of(runner_id, view.tenant_id, record, identity)
        if standing is HostStanding.NOT_HELD:
            raise HostNotHolding(
                f"the hub holds runner {entry.name} ({runner_id}) {reason}: take it under this host first with "
                f"`blizzard hub runner-host adopt {identity.host_id} {runner_id}`"
            )
        if standing is HostStanding.TENANT_DIFFERS:
            raise HostNotHolding(f"runner {entry.name} ({runner_id}) {reason}, so this host cannot take it in")
        if runner_id not in view.named_runners:
            raise SourceUnsupported(f"the source store {source.store} holds no runner {entry.name} ({runner_id})")
        if source.kind is not SourceKind.UNGRADUATED and not copy.runner_scoped:
            raise SourceUnsupported(
                f"the source store {source.store} predates the revision {_RUNNER_SCOPE_REVISION}, so one runner's rows "
                "cannot be removed from it by themselves: run a blizzard release that migrates it, then re-run"
            )
        refuse_busy(view, runner_name=entry.name)
        if source.config is not None:
            refuse_declared_here(
                tuple(DeclaredRunner(held.name, held.id) for held in self.host.runners),
                runner_name=entry.name,
                runner_id=runner_id,
                config_path=str(self.host.config_path),
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
        """This host's identity as the hub's ``record`` names it, refused when the host holds another. Only the host's
        own registration records it."""
        held = self.stores.host_identity(self.host.db_url)
        try:
            return adopted(held, record.host_id, record.tenant_id, self.clock.now())
        except HostIdentityAlreadyRecorded as exc:
            raise HostNotHolding(exc.refusal()) from exc

    def _plan(self, source: _Source, entry: RunnerEntry, workspace_id: str | None) -> _ConfigPlan:
        """The configuration edits the runner brings; an archive's are all made already."""
        if source.config is None:
            refuse_archive_rename(workspace_id)
            return _ConfigPlan()
        config = source.config
        workspaces, ids = self._workspace_plan(config, entry, workspace_id)
        declared = {subscription.slug for subscription in self.host.subscriptions}
        subscription = (
            next((s for s in config.subscriptions if s.slug == entry.subscription), None)
            if entry.subscription is not None and entry.subscription not in declared
            else None
        )
        table, files = self._runner_plan(config, entry, ids, text=source.host_text)
        token = self.tokens.held(config.root, source.token_variable(entry.name))
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
        theirs = {workspace.id: workspace for workspace in config.workspaces}
        placements = place_workspaces(
            tuple(dict.fromkeys(binding.workspace for binding in entry.bindings)),
            runner_name=entry.name,
            workspace_id=workspace_id,
            offered={held_id: _shape(workspace, config.root) for held_id, workspace in theirs.items()},
            held={workspace.id: _shape(workspace, self.host.root) for workspace in self.host.workspaces},
        )
        appended: list[HostWorkspace] = []
        for placement in placements:
            if placement.appended:
                offered = theirs[placement.name]
                appended.append(
                    replace(
                        offered,
                        id=placement.target,
                        root=_shape(offered, config.root).root,
                        prompt_file=_absolute(offered.prompt_file, config.root),
                    )
                )
        ids = {placement.name: placement.target for placement in placements if placement.target != placement.name}
        return tuple(appended), ids

    def _runner_plan(
        self, config: HostConfig, entry: RunnerEntry, ids: Mapping[str, str], *, text: str | None
    ) -> tuple[str, tuple[tuple[Path, Path], ...]]:
        """The source's ``[[runner]]`` text with its bindings renamed and every path key pointing into the source
        rewritten to its copy under this host's ``runners/<name>/``, and the copies to make."""
        try:
            table = runner_table(config.layout, entry.name, strict=self.host.strict, text=text)
        except ConfigError as exc:
            raise SourceUnsupported(str(exc)) from exc
        values: dict[tuple[str, ...], str] = {}
        if ids:
            values[("projects",)] = _bindings_literal(
                tuple(ProjectBinding(b.project, ids.get(b.workspace, b.workspace), b.agents) for b in entry.bindings)
            )
        files: list[tuple[Path, Path]] = []
        runner_dir = self.host.root / RUNNERS_DIRNAME / entry.name
        runner = _runner_of(table, strict=self.host.strict)
        for path_key in RUNNER_PATH_KEYS:
            value = _lookup(runner, path_key.key)
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
            return with_runner_values(table, values, strict=self.host.strict), tuple(files)
        except ConfigError as exc:
            raise SourceUnsupported(f"runner {entry.name}'s [[runner]] cannot be carried: {exc}") from exc

    def _refuse_conflicts(
        self, source: _Source, entry: RunnerEntry, runner_id: str, view: RunnerStoreView, plan: _ConfigPlan
    ) -> None:
        """The refusals that need the configuration plan: a held environment two runners would hold, and a token that
        cannot move."""
        refuse_binding_conflict(
            view.held_bindings,
            self.stores.held_bindings(self.host.db_url),
            renamed=plan.workspace_ids or {},
            runner_name=entry.name,
        )
        variable = hub_token_variable(entry.name)
        refuse_token_conflict(
            carries_runner=plan.runner_table is not None,
            offered=plan.token,
            held_here=self.tokens.held(self.host.root, variable),
            source_variable=source.token_variable(entry.name),
            variable=variable,
            runner_name=entry.name,
            runner_id=runner_id,
        )

    def _configure(self, source: _Source, entry: RunnerEntry, plan: _ConfigPlan) -> None:
        """Make each configuration edit not yet made, in the order a crash between any two leaves resumable."""
        layout, strict = self.host.layout, self.host.strict
        for workspace in plan.workspaces:
            if workspace.id not in _declared(layout, "workspace", "id", strict=strict):
                self.host_file.append_workspace(layout, workspace_toml(workspace))
        if plan.subscription is not None and plan.subscription.slug not in _declared(
            layout, "subscription", "slug", strict=strict
        ):
            self.host_file.append_subscription(layout, subscription_toml(plan.subscription))
        if plan.runner_table is not None:
            for located, copied in plan.files:
                if located.is_dir():
                    shutil.copytree(located, copied, dirs_exist_ok=True)
                elif located.exists():
                    copied.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(located, copied)
            (self.host.root / RUNNERS_DIRNAME / entry.name).mkdir(parents=True, exist_ok=True)
            if entry.name not in _declared(layout, "runner", "name", strict=strict):
                self.host_file.append_runner(layout, plan.runner_table)
        variable = hub_token_variable(entry.name)
        if plan.token and self.tokens.held(self.host.root, variable) != plan.token:
            self.tokens.write(self.host.root, variable, plan.token)
        if source.config is not None:
            self.tokens.remove(source.config.root, source.token_variable(entry.name))

    def _set_aside_emptied(self, source: _Source) -> tuple[Path, ...]:
        """The source store moved aside whole when no runner is left in it."""
        if not source.store.exists() or self.stores.named_runners(sqlite_url(source.store)):
            return ()
        return set_aside_sqlite(source.store, now=self.clock.now()).moved


def _shape(workspace: HostWorkspace, root: Path) -> WorkspaceShape:
    """``workspace``'s shape, its root resolved against ``root``, the host that declared it."""
    return WorkspaceShape(workspace.settings(root).effective_workspace_root, frozenset(workspace.envs))


def _absolute(prompt_file: str, root: Path) -> str:
    """``prompt_file`` as a path that means the same on another host: a relative one resolved against ``root``, the host
    that declared it."""
    if not prompt_file:
        return ""
    located = Path(prompt_file)
    return str(located if located.is_absolute() else root / located)


def _declared(layout: HostLayout, table: str, key: str, *, strict: bool) -> set[str]:
    """The ``key`` of every ``[[table]]`` the host file holds now."""
    document = decode_host_text(layout.config_path.read_text(), what=str(layout.config_path))
    view = ConfigView.root(HOST_REGISTRY, document, strict=strict)
    return {str(entry.get(key)) for entry in view.entries(table) if entry.body}


def _runner_of(table: str, *, strict: bool) -> ConfigView:
    """The one ``[[runner]]`` a runner table holds, as a view over the host registry."""
    view = ConfigView.root(HOST_REGISTRY, decode_host_text(table, what="the [[runner]]"), strict=strict)
    return next(view.entries("runner"))


def _lookup(runner: ConfigView, key: tuple[str, ...]) -> object:
    node = runner
    for part in key[:-1]:
        node = node.table(part)
    return node.get(key[-1])


def _string_literal(value: str) -> str:
    return json.dumps(value)


def _bindings_literal(bindings: tuple[ProjectBinding, ...]) -> str:
    entries = (
        f"{{ project = {_string_literal(b.project)}, workspace = {_string_literal(b.workspace)}"
        + ("" if b.agents is None else f", agents = {b.agents}")
        + " }"
        for b in bindings
    )
    return "[" + ", ".join(entries) + "]"
