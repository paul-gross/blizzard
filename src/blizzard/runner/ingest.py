"""``blizzard runner ingest`` — move one runner from another store into this host: its rows, configuration and token.

The source — a host directory, a reset archive, or a runner directory that has not graduated — is read only through a
migrated backup copy until its rows go. The copy, each configuration edit, and the removal touch separate files, so each
edit is skipped when made already and a re-run resumes from the ``host_ingests`` record. The order and windows are
recorded in ``bzh:crash-exemptions-runner``."""

from __future__ import annotations

from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.foundation.runner_token_names import hub_token_variable
from blizzard.foundation.store.migrations import MigrationConnectionError, MigrationRefused, MigrationRunner
from blizzard.runner.config_table import ConfigError
from blizzard.runner.host_layout import RUNNERS_DIRNAME
from blizzard.runner.hub.client import HostRecord, HostRefused, HubClientError, IHostRecordReader
from blizzard.runner.hub.host_identity import HostIdentity, HostIdentityAlreadyRecorded, adopted
from blizzard.runner.hub.host_registration import standing_of
from blizzard.runner.hub.identity import HostStanding
from blizzard.runner.transfer.host_files import IHostFiles, NotAStoreFile
from blizzard.runner.transfer.hosts import (
    DeclaredBinding,
    DeclaredSubscription,
    DeclaredWorkspace,
    IngestHost,
    ISourceHosts,
    RunnerDeclaration,
    SourceHost,
)
from blizzard.runner.transfer.paths import rebase_path_keys
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
    refuse_standing,
    refuse_token_conflict,
    refuse_unscoped_source,
    resumes,
)
from blizzard.runner.transfer.runner_stores import (
    HeldBinding,
    IRunnerBoundReadsFactory,
    IRunnerBoundStoresFactory,
    RunnerStoreView,
)
from blizzard.runner.transfer.source import SourceKind

__all__ = [
    "INGEST_COMMAND",
    "IHostFileEditor",
    "IIngestStores",
    "ITokenFiles",
    "IngestOutcome",
    "IngestRequest",
    "RunnerIngest",
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

#: The revision from which every runner-owned table carries its runner, so one runner's rows can be removed from a
#: store that holds others.
_RUNNER_SCOPE_REVISION = "20261015_1000_host_ingests"

_log = get_logger("blizzard.runner.ingest")


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
    """The host-file edits an ingest makes to the host at ``root``, and the re-read that tells whether one is made."""

    def declared(self, root: Path, table: str, key: str) -> frozenset[str]:
        """The ``key`` of every ``[[table]]`` the host file holds now."""
        ...

    def append_workspace(self, root: Path, workspace: DeclaredWorkspace) -> None: ...
    def append_subscription(self, root: Path, subscription: DeclaredSubscription) -> None: ...
    def append_runner(self, root: Path, table: str) -> None: ...


class ITokenFiles(Protocol):
    """A runner token in a host directory's ``.env``."""

    def held(self, root: Path, token_env: str) -> str:
        """The token ``.env`` assigns ``token_env``, or ``""``."""
        ...

    def write(self, root: Path, token_env: str, token: str) -> None: ...
    def remove(self, root: Path, token_env: str) -> None: ...


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

    workspaces: tuple[DeclaredWorkspace, ...] = ()
    workspace_ids: Mapping[str, str] | None = None
    subscription: DeclaredSubscription | None = None
    runner_table: str | None = None
    files: tuple[tuple[Path, Path], ...] = ()
    token: str = ""


@dataclass(frozen=True)
class RunnerIngest:
    """Ingests runners into the host ``host``."""

    host: IngestHost
    sources: ISourceHosts
    stores: IIngestStores
    host_file: IHostFileEditor
    tokens: ITokenFiles
    hub: IHostRecordReader
    #: What answers on a socket path, if anything.
    holder: Callable[[Path], str | None]
    files: IHostFiles
    clock: IClock
    #: The migrations of the runner store at a source copy's URL.
    migrations: Callable[[str], MigrationRunner]

    def run(self, request: IngestRequest) -> IngestOutcome:
        """Ingest the runner ``request`` names, or complete an ingest of it interrupted part-way. Raises an
        :class:`IngestRefused` naming why it cannot."""
        kind = self.sources.kind(request.source)
        path = request.source.resolve()
        if path == self.host.root:
            raise SourceUnsupported(f"{path} is this host; name another host directory or an archive store")
        with (
            self._held(self.host.root, "this host"),
            self._held(path, "the source") if kind is SourceKind.HOST_DIRECTORY else nullcontext(),
            self._answering(self.sources.legacy_socket(path)) if kind is SourceKind.UNGRADUATED else nullcontext(),
        ):
            source = self._source(kind, path)
            return self._ingest(source, request)

    @contextmanager
    def _held(self, root: Path, which: str) -> Generator[None, None, None]:
        """The host directory ``root`` held for the run: its lock taken, and nothing answering on its sockets."""
        with self.files.hold(
            root,
            self.holder,
            lambda held: SourceRunning(f"{which} is running ({held}); stop it, then re-run"),
        ):
            yield

    @contextmanager
    def _answering(self, socket: Path) -> Generator[None, None, None]:
        """A runner directory that has not graduated, held for the run: a pre-host runner takes no lock, so only
        its ``runner.sock`` tells whether it runs."""
        if (held := self.holder(socket)) is not None:
            raise SourceRunning(f"the source is running ({held}); stop it, then re-run")
        yield

    def _source(self, kind: SourceKind, path: Path) -> SourceHost:
        if kind is SourceKind.ARCHIVE:
            return SourceHost(kind, path, path, None)
        if kind is SourceKind.UNGRADUATED:
            return self._ungraduated(path)
        return self.sources.open_host(path)

    def _ungraduated(self, path: Path) -> SourceHost:
        """A runner directory that has not graduated, read as the host of one it graduates to: its store is
        ``data/runner.db``, its runner's id is the one that store records, and its token is under the variable its
        file names. Once its store is moved aside it carries nothing but its path, which an ingest record names."""
        store, token_env = self.sources.legacy_store(path)
        if not store.exists():
            return SourceHost(SourceKind.UNGRADUATED, path, store, None, token_env=token_env)
        with self._copy_of(store) as copy:
            named_runners = self.stores.named_runners(copy.url)
            if len(named_runners) != 1:
                raise SourceUnsupported(
                    f"the source store {store} records {len(named_runners)} runner identities "
                    f"({', '.join(named_runners) or 'none'}), not the one a runner before hosts holds"
                )
            [runner_id] = named_runners
            view = self.stores.reads_for(runner_id).runner_view(copy.url)
        return self.sources.graduate(path, runner_id=runner_id, served=view.served_projects)

    def _entry(self, source: SourceHost, runner: str) -> RunnerDeclaration:
        """The moving runner's ``[[runner]]``: the source's, or — for an archive, which carries rows only — this
        host's."""
        declared = source.declared or self.host.declared
        try:
            entry = self.sources.runner(source if source.declared is not None else None, runner)
        except ConfigError as exc:
            if source.kind is SourceKind.UNGRADUATED:
                raise SourceUnsupported(
                    f"the source holds no store at {source.store}, and this host holds no runner {runner} ingested "
                    f"from it: {exc}"
                ) from exc
            if source.declared is None:
                raise SourceUnsupported(
                    f"an archive store carries the runner's rows only, so its [[runner]] and token must already be in "
                    f"this host: {exc}"
                ) from exc
            raise SourceUnsupported(str(exc)) from exc
        if entry.id is None:
            raise SourceUnsupported(f"runner {entry.name} has no id in {declared.config_path}, so the hub holds none")
        if entry.errors:
            raise SourceUnsupported(f"runner {entry.name}'s [[runner]] is refused: {'; '.join(entry.errors)}")
        return entry

    def _ingest(self, source: SourceHost, request: IngestRequest) -> IngestOutcome:
        entry = self._entry(source, request.runner)
        runner_id = entry.id
        assert runner_id is not None
        here = self.host.db_url
        try:
            self.files.store_file(here)
        except NotAStoreFile as exc:
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
            moved = self.files.set_aside(source.store, now=self.clock.now())
        else:
            runner.remove_runner_rows(self.files.store_url(source.store), keep_counters=False)
            moved = self._set_aside_emptied(source)
        _log.info("runner ingested", runner_id=runner_id, source=str(source.path), resumed=resuming)
        return IngestOutcome(runner_id, entry.name, resumed=resuming, set_aside=moved)

    @contextmanager
    def _copy_of(self, store: Path) -> Generator[_SourceCopy, None, None]:
        """A backup copy of the source ``store`` beside this host's store, migrated to head, removed on leaving."""
        with self.files.copy_of(store, self.host.data_dir) as copy:
            migrations = self.migrations(self.files.store_url(copy))
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
            yield _SourceCopy(self.files.store_url(copy), runner_scoped)

    def _refuse_fresh(
        self, source: SourceHost, entry: RunnerDeclaration, runner_id: str, view: RunnerStoreView, copy: _SourceCopy
    ) -> None:
        """Every refusal a first ingest of the runner earns before anything is written."""
        record = self._hub_record()
        identity = self._host_identity(record)
        standing, reason = standing_of(runner_id, view.tenant_id, record, identity)
        refuse_standing(
            not_held=standing is HostStanding.NOT_HELD,
            tenant_differs=standing is HostStanding.TENANT_DIFFERS,
            reason=reason,
            runner_name=entry.name,
            runner_id=runner_id,
            host_id=identity.host_id,
        )
        refuse_unscoped_source(
            view,
            runner_name=entry.name,
            runner_id=runner_id,
            source_store=str(source.store),
            ungraduated=source.kind is SourceKind.UNGRADUATED,
            runner_scoped=copy.runner_scoped,
            scope_revision=_RUNNER_SCOPE_REVISION,
        )
        refuse_busy(view, runner_name=entry.name)
        if source.declared is not None:
            refuse_declared_here(
                tuple(DeclaredRunner(held.name, held.id) for held in self.host.declared.runners),
                runner_name=entry.name,
                runner_id=runner_id,
                config_path=str(self.host.declared.config_path),
            )

    def _hub_record(self) -> HostRecord:
        hub = self.host.hub_url
        if not self.host.holds_host_token:
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

    def _plan(self, source: SourceHost, entry: RunnerDeclaration, workspace_id: str | None) -> _ConfigPlan:
        """The configuration edits the runner brings; an archive's are all made already."""
        if source.declared is None:
            refuse_archive_rename(workspace_id)
            return _ConfigPlan()
        theirs = source.declared
        workspaces, ids = self._workspace_plan(theirs.workspaces, theirs.root, entry, workspace_id)
        declared = {subscription.slug for subscription in self.host.declared.subscriptions}
        subscription = (
            next((s for s in theirs.subscriptions if s.slug == entry.subscription), None)
            if entry.subscription is not None and entry.subscription not in declared
            else None
        )
        table, files = self._runner_plan(source, entry, ids)
        token = self.tokens.held(theirs.root, source.token_variable(entry.name))
        return _ConfigPlan(
            workspaces=workspaces,
            workspace_ids=ids or None,
            subscription=subscription,
            runner_table=table,
            files=files,
            token=token,
        )

    def _workspace_plan(
        self,
        offered: tuple[DeclaredWorkspace, ...],
        source_root: Path,
        entry: RunnerDeclaration,
        workspace_id: str | None,
    ) -> tuple[tuple[DeclaredWorkspace, ...], dict[str, str]]:
        """The workspaces to append and the ids the runner's workspaces take here."""
        theirs = {workspace.id: workspace for workspace in offered}
        placements = place_workspaces(
            tuple(dict.fromkeys(binding.workspace for binding in entry.bindings)),
            runner_name=entry.name,
            workspace_id=workspace_id,
            offered={held_id: _shape(workspace) for held_id, workspace in theirs.items()},
            held={workspace.id: _shape(workspace) for workspace in self.host.declared.workspaces},
        )
        appended: list[DeclaredWorkspace] = []
        for placement in placements:
            if placement.appended:
                taken = theirs[placement.name]
                appended.append(
                    replace(
                        taken,
                        id=placement.target,
                        root=taken.effective_root,
                        prompt_file=_absolute(taken.prompt_file, source_root),
                    )
                )
        ids = {placement.name: placement.target for placement in placements if placement.target != placement.name}
        return tuple(appended), ids

    def _runner_plan(
        self, source: SourceHost, entry: RunnerDeclaration, ids: Mapping[str, str]
    ) -> tuple[str, tuple[tuple[Path, Path], ...]]:
        """The source's ``[[runner]]`` text with its bindings renamed and every path key pointing into the source
        rewritten to its copy under this host's ``runners/<name>/``, and the copies to make."""
        projects = (
            tuple(DeclaredBinding(b.project, ids.get(b.workspace, b.workspace)) for b in entry.bindings)
            if ids
            else None
        )
        located = self.sources.path_keys(source, entry.name)
        copies, files = rebase_path_keys(
            located,
            tuple(located),
            source.declared.root if source.declared is not None else Path(),
            Path(RUNNERS_DIRNAME) / entry.name,
            self.host.runners_dir / entry.name,
        )
        return self.sources.carried(source, entry.name, projects=projects, copies=copies), files

    def _refuse_conflicts(
        self, source: SourceHost, entry: RunnerDeclaration, runner_id: str, view: RunnerStoreView, plan: _ConfigPlan
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

    def _configure(self, source: SourceHost, entry: RunnerDeclaration, plan: _ConfigPlan) -> None:
        """Make each configuration edit not yet made, in the order a crash between any two leaves resumable."""
        root = self.host.root
        for workspace in plan.workspaces:
            if workspace.id not in self.host_file.declared(root, "workspace", "id"):
                self.host_file.append_workspace(root, workspace)
        if plan.subscription is not None and plan.subscription.slug not in self.host_file.declared(
            root, "subscription", "slug"
        ):
            self.host_file.append_subscription(root, plan.subscription)
        if plan.runner_table is not None:
            for located, copied in plan.files:
                if located.is_dir():
                    self.files.copy_tree(located, copied)
                elif located.exists():
                    self.files.copy_file(located, copied)
            (self.host.runners_dir / entry.name).mkdir(parents=True, exist_ok=True)
            if entry.name not in self.host_file.declared(root, "runner", "name"):
                self.host_file.append_runner(root, plan.runner_table)
        variable = hub_token_variable(entry.name)
        if plan.token and self.tokens.held(root, variable) != plan.token:
            self.tokens.write(root, variable, plan.token)
        if source.declared is not None:
            self.tokens.remove(source.declared.root, source.token_variable(entry.name))

    def _set_aside_emptied(self, source: SourceHost) -> tuple[Path, ...]:
        """The source store moved aside whole when no runner is left in it."""
        if not source.store.exists() or self.stores.named_runners(self.files.store_url(source.store)):
            return ()
        return self.files.set_aside(source.store, now=self.clock.now())


def _shape(workspace: DeclaredWorkspace) -> WorkspaceShape:
    """``workspace``'s shape, its root resolved against the host that declared it."""
    return WorkspaceShape(workspace.effective_root, frozenset(workspace.envs))


def _absolute(prompt_file: str, root: Path) -> str:
    """``prompt_file`` as a path that means the same on another host: a relative one resolved against ``root``, the host
    that declared it."""
    if not prompt_file:
        return ""
    located = Path(prompt_file)
    return str(located if located.is_absolute() else root / located)
