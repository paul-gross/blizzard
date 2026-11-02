"""Composition of the participants confined to one config apply transaction."""

from collections.abc import Generator
from contextlib import contextmanager

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.capabilities import IForgeOffers
from blizzard.hub.domain.config.carry_over import IConfigImportWriter
from blizzard.hub.domain.config.secrets import ISecretCipher
from blizzard.hub.domain.config_apply.service import ApplySession
from blizzard.hub.domain.projects.color import ColorPick
from blizzard.hub.store.internal.config_apply_store import BorrowedConfigApplyWriter
from blizzard.hub.store.internal.config_authoring_lock import lock_config_authoring
from blizzard.hub.store.internal.forge_record_store import ForgeRecordStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.project_link_store import ProjectLinkStore
from blizzard.hub.store.internal.project_store import ProjectStore
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.store.internal.routine_scope_store import RoutineScopeStore
from blizzard.hub.store.internal.routine_store import RoutineStore
from blizzard.hub.store.internal.scope_store import ScopeStore
from blizzard.hub.store.internal.secret_store import SecretStore
from blizzard.hub.store.internal.work_source_record_store import WorkSourceRecordStore
from blizzard.hub.store.scoped import ScopedConnections


class _ApplyRollback(Exception):
    """End a dry run without committing its transaction."""


class ApplyParticipants:
    """Build apply's read adapters and writer over its locked connection."""

    def __init__(
        self,
        connections: ScopedConnections,
        *,
        imports: IConfigImportWriter,
        cipher: ISecretCipher,
        offers: IForgeOffers,
        pick: ColorPick,
    ) -> None:
        self._connections = connections
        self._imports = imports
        self._cipher = cipher
        self._offers = offers
        self._pick = pick

    @contextmanager
    def open(self, *, dry_run: bool, clock: IClock) -> Generator[ApplySession, None, None]:
        try:
            with self._connections.write("config apply", expect=(_ApplyRollback,)) as conn:
                lock_config_authoring(conn)
                scoped = self._connections.borrow(conn)
                repositories = RepositoryRecordStore(scoped)
                sources = WorkSourceRecordStore(scoped)
                forges = ForgeRecordStore(scoped)
                secrets = SecretStore(scoped)
                authoring = ConfigAuthoring(
                    work_sources=sources,
                    repositories=repositories,
                    forges=forges,
                    offers=self._offers,
                    secrets=secrets,
                    cipher=self._cipher,
                    apply_writer=BorrowedConfigApplyWriter(conn),
                    import_writer=self._imports,
                    events=None,
                    clock=clock,
                )
                yield ApplySession(
                    authoring=authoring,
                    repositories=repositories,
                    work_sources=sources,
                    forges=forges,
                    projects=ProjectStore(scoped),
                    links=ProjectLinkStore(scoped),
                    scopes=ScopeStore(scoped),
                    routines=RoutineStore(scoped),
                    routine_scopes=RoutineScopeStore(scoped),
                    graphs=GraphStore(scoped),
                    secrets=secrets,
                    pick=self._pick,
                    clock=clock,
                )
                if dry_run:
                    raise _ApplyRollback
        except _ApplyRollback:
            pass
