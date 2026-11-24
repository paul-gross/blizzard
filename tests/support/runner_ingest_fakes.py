"""In-memory doubles of every port :class:`~blizzard.runner.transfer.ingest.RunnerIngest` takes, so an ingest runs
without a disk, a store, or a hub. Each one appends what it does to one shared ``journal``, in call order."""

from __future__ import annotations

from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.secrets.sealing import ISecretCipher, SealedSecret, SealedValue, SecretUnreadable
from blizzard.foundation.secrets.value import SecretValue
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner.config_table import ConfigError
from blizzard.runner.hub.client import HostHeldRunner, HostRecord, HostRefused
from blizzard.runner.hub.host_identity import HostIdentity
from blizzard.runner.hub.sealed_tokens import SealedHubToken
from blizzard.runner.transfer.host_files import IHostFiles, NotAStoreFile
from blizzard.runner.transfer.hosts import (
    DeclaredBinding,
    DeclaredSubscription,
    DeclaredWorkspace,
    HeldRecords,
    HostDeclarations,
    IngestHost,
    ISourceHosts,
    RunnerDeclaration,
    SourceHost,
)
from blizzard.runner.transfer.ingest import IHostCiphers, IHostFileEditor, IIngestStores, ITokenFiles, RunnerIngest
from blizzard.runner.transfer.rows import HostIngest
from blizzard.runner.transfer.runner_stores import HeldBinding, IRunnerBoundStores, RunnerStoreView
from blizzard.runner.transfer.source import SourceKind

T0 = datetime(2026, 1, 1, tzinfo=UTC)
HOST_ID = "hs_here"
TENANT = "ten_default"
RUNNER = "r2"
RUNNER_ID = "rn_moving"
HERE = Path("/hosts/here")
HERE_URL = "fake:///hosts/here/data/host.db"
#: The scheme of every store URL :class:`FakeHostFiles` names; any other names no store file.
_SCHEME = "fake://"


def _url(store: Path) -> str:
    return f"{_SCHEME}{store}"


@dataclass
class FakeHostFiles:
    """A host disk held in memory: the ``files`` and ``directories`` on it, and the ``links`` resolution follows —
    a link to a link followed to its end. A copy or a made directory lands in the sets; a set-aside store leaves them.
    A ``held`` root refuses its hold."""

    journal: list[tuple[object, ...]]
    files: set[Path] = field(default_factory=set[Path])
    directories: set[Path] = field(default_factory=set[Path])
    links: dict[Path, Path] = field(default_factory=dict[Path, Path])
    held: frozenset[Path] = frozenset()

    def store_file(self, db_url: str) -> Path:
        if not db_url.startswith(_SCHEME):
            raise NotAStoreFile(f"{db_url} names no SQLite file")
        return Path(db_url.removeprefix(_SCHEME))

    def store_url(self, database: Path) -> str:
        return _url(database)

    def resolved(self, path: Path) -> Path:
        self.journal.append(("resolved", path))
        while path in self.links:
            path = self.links[path]
        return path

    def exists(self, path: Path) -> bool:
        return path in self.files or path in self.directories

    @contextmanager
    def copy_of(self, store: Path, beside: Path) -> Generator[Path, None, None]:
        self.journal.append(("copy_of", store))
        yield beside / "copy" / store.relative_to(store.anchor)

    def copy_path(self, src: Path, dst: Path) -> None:
        if src in self.directories:
            self.journal.append(("copy_tree", src, dst))
            self.directories.add(dst)
        elif src in self.files:
            self.journal.append(("copy_file", src, dst))
            self.directories.add(dst.parent)
            self.files.add(dst)

    def make_directory(self, path: Path) -> None:
        self.journal.append(("make_directory", path))
        self.directories.add(path)

    def set_aside(self, store: Path, *, now: datetime) -> tuple[Path, ...]:
        self.journal.append(("set_aside", store))
        self.files.discard(store)
        return (store.with_name(f"{store.name}.aside"),)

    @contextmanager
    def hold(
        self, root: Path, probe: Callable[[Path], str | None], refusal: Callable[[str], Exception]
    ) -> Generator[None, None, None]:
        if root in self.held:
            raise refusal("pid 1")
        self.journal.append(("hold", root))
        yield


@dataclass
class FakeSourceHosts:
    """Sources by path: a graduated host directory declaring ``RUNNER``, or a runner directory not yet graduated,
    its store under ``data/runner.db``. ``path_keys`` are the moving runner's path keys."""

    journal: list[tuple[object, ...]]
    kinds: Mapping[Path, SourceKind]
    path_keys_set: Mapping[tuple[str, ...], Path] = field(default_factory=dict[tuple[str, ...], Path])

    def kind(self, source: Path) -> SourceKind:
        return self.kinds[source]

    def legacy_socket(self, root: Path) -> Path:
        return root / "runner.sock"

    def open_host(self, path: Path) -> SourceHost:
        self.journal.append(("open_host", path))
        return SourceHost(SourceKind.HOST_DIRECTORY, path, source_store(path), declarations(path, (RUNNER,)))

    def legacy_store(self, path: Path) -> tuple[Path, str]:
        return path / "data" / "runner.db", "BLIZZARD_HUB_TOKEN"

    def graduate(self, path: Path, *, runner_id: str, served: tuple[str, ...]) -> SourceHost:
        store, token_env = self.legacy_store(path)
        return SourceHost(SourceKind.UNGRADUATED, path, store, declarations(path, (RUNNER,)), token_env=token_env)

    def runner(self, source: SourceHost | None, name: str) -> RunnerDeclaration:
        if source is None or name != RUNNER:
            raise ConfigError(f"no [[runner]] {name}")
        return RunnerDeclaration(RUNNER, RUNNER_ID)

    def path_keys(self, source: SourceHost, runner: str) -> Mapping[tuple[str, ...], Path]:
        return self.path_keys_set

    def carried(
        self,
        source: SourceHost,
        runner: str,
        *,
        projects: tuple[DeclaredBinding, ...] | None,
        copies: Mapping[tuple[str, ...], Path],
    ) -> str:
        return f'[[runner]]\nname = "{runner}"\n'


def source_store(root: Path) -> Path:
    return root / "data" / "host.db"


def declarations(root: Path, runners: tuple[str, ...]) -> HostDeclarations:
    return HostDeclarations(
        root=root,
        config_path=root / "blizzard.toml",
        runners=tuple(RunnerDeclaration(name) for name in runners),
        workspaces=(),
        subscriptions=(),
    )


@dataclass
class FakeRunnerStores:
    """One runner's rows across the stores of :class:`FakeIngestStores`."""

    runner_id: str
    stores: FakeIngestStores

    def runner_view(self, url: str) -> RunnerStoreView:
        named = self.stores.named[self.stores.source_of(url)]
        return RunnerStoreView(
            named_runners=named,
            tenant_id=TENANT,
            open_elicitation_lease_ids=frozenset(),
            open_takeover_chunk_ids=frozenset(),
            held_bindings=(),
            last_alive_at=T0,
        )

    def latest_host_ingest(self, url: str) -> HostIngest | None:
        return self.stores.ingests.get(url)

    def sealed_hub_token(self, url: str) -> SealedHubToken | None:
        return self.stores.hub_tokens.get(self.stores.source_of(url))

    def copy_runner(
        self,
        source_url: str,
        destination_url: str,
        *,
        workspace_ids: Mapping[str, str] | None = None,
        bound_workspaces: Sequence[str] = (),
        ingest: HostIngest | None = None,
        hub_token: SealedValue | None = None,
    ) -> None:
        assert ingest is not None
        self.stores.journal.append(("copy_runner", ingest.source))
        self.stores.named[destination_url] = (*self.stores.named.get(destination_url, ()), self.runner_id)
        self.stores.ingests[destination_url] = ingest
        if hub_token is not None and destination_url not in self.stores.hub_tokens:
            self.stores.hub_tokens[destination_url] = SealedHubToken(self.runner_id, 1, hub_token, T0)

    def remove_runner_rows(self, url: str, *, keep_counters: bool, drop_host: bool = False) -> None:
        self.stores.journal.append(("remove_runner_rows", url))
        if drop_host:
            self.stores.hub_tokens.pop(url, None)
        self.stores.named[url] = tuple(r for r in self.stores.named.get(url, ()) if r != self.runner_id)
        self.stores.on_remove(url)


@dataclass
class FakeIngestStores:
    """Stores by URL: the runners each names, and the ingests each recorded. A copy's URL reads its source's rows."""

    journal: list[tuple[object, ...]]
    named: dict[str, tuple[str, ...]] = field(default_factory=dict[str, tuple[str, ...]])
    ingests: dict[str, HostIngest] = field(default_factory=dict[str, HostIngest])
    #: The moving runner's sealed hub token each store holds.
    hub_tokens: dict[str, SealedHubToken] = field(default_factory=dict[str, SealedHubToken])
    #: Called with a store's URL once a runner's rows leave it.
    on_remove: Callable[[str], None] = lambda _url: None

    def source_of(self, url: str) -> str:
        """The URL of the store a copy's ``url`` copies — the copy names it under its ``copy/`` — or ``url`` itself."""
        _, copied, store = url.partition("/copy/")
        return _url(Path("/", store)) if copied else url

    def named_runners(self, url: str) -> tuple[str, ...]:
        return self.named.get(self.source_of(url), ())

    def host_identity(self, url: str) -> HostIdentity | None:
        return None

    def held_bindings(self, url: str) -> tuple[HeldBinding, ...]:
        return ()

    def sealed_key_ids(self, url: str) -> frozenset[str]:
        held = self.hub_tokens.get(self.source_of(url))
        return frozenset() if held is None else frozenset({held.sealed.key_id})

    def reads_for(self, runner_id: str) -> FakeRunnerStores:
        return FakeRunnerStores(runner_id, self)

    def for_runner(self, runner_id: str) -> IRunnerBoundStores:
        return FakeRunnerStores(runner_id, self)


@dataclass
class FakeHostFileEditor:
    journal: list[tuple[object, ...]]
    tables: dict[tuple[str, str], set[str]] = field(default_factory=dict[tuple[str, str], set[str]])

    def declared(self, root: Path, table: str, key: str) -> frozenset[str]:
        return frozenset(self.tables.get((table, key), set()))

    def append_workspace(self, root: Path, workspace: DeclaredWorkspace) -> None:
        self.journal.append(("append_workspace", workspace.id))

    def append_subscription(self, root: Path, subscription: DeclaredSubscription) -> None:
        self.journal.append(("append_subscription", subscription.slug))

    def append_runner(self, root: Path, table: str) -> None:
        self.journal.append(("append_runner", root))
        self.tables.setdefault(("runner", "name"), set()).add(RUNNER)


@dataclass
class FakeTokenFiles:
    tokens: dict[tuple[Path, str], str] = field(default_factory=dict[tuple[Path, str], str])

    def held(self, root: Path, token_env: str) -> str:
        return self.tokens.get((root, token_env), "")

    def remove(self, root: Path, token_env: str) -> None:
        self.tokens.pop((root, token_env), None)


@dataclass(frozen=True)
class FakeCipher:
    """A cipher under one key generation, ``key_id``: a value "sealed" in the clear, bound to its name and revision."""

    key_id: str

    def seal(self, value: SecretValue, *, name: str, revision: int) -> SealedValue:
        return SealedValue(self.key_id, value.expose().encode(), f"{name}@{revision}".encode())

    def open(self, secret: SealedSecret) -> SecretValue:
        sealed = secret.sealed
        if sealed.key_id != self.key_id or sealed.nonce != f"{secret.name}@{secret.revision}".encode():
            raise SecretUnreadable(secret.name, key_id=sealed.key_id)
        return SecretValue(sealed.ciphertext.decode())


@dataclass
class FakeHostCiphers:
    """Each host's key by its root; a minted key is named for the host it was minted on."""

    journal: list[tuple[object, ...]]
    keys: dict[Path, str] = field(default_factory=dict[Path, str])

    #: The key the invoking environment carries, which a source's row may also open under.
    environ_key: str | None = None

    def cipher(self, root: Path) -> ISecretCipher | None:
        return FakeCipher(self.keys[root]) if root in self.keys else None

    def sealing_cipher(self, root: Path, *, sealed_under: frozenset[str]) -> ISecretCipher:
        if root not in self.keys:
            self.journal.append(("mint_key", root))
            self.keys[root] = f"key-{root.name}"
        return FakeCipher(self.keys[root])

    def source_ciphers(self, root: Path) -> tuple[ISecretCipher, ...]:
        held = (self.keys.get(root), self.environ_key)
        return tuple(FakeCipher(key) for key in held if key is not None)


@dataclass(frozen=True)
class FakeHostRecordReader:
    """A hub holding ``RUNNER_ID`` under this host."""

    def record(self) -> HostRecord | HostRefused:
        return HostRecord(HOST_ID, TENANT, "here", runners=(HostHeldRunner(RUNNER_ID, RUNNER),))


@dataclass(frozen=True)
class FakeMigrations(MigrationRunner):
    """A source copy already at every revision an ingest asks after; migrating it does nothing."""

    def current_revision(self) -> str | None:
        return "head"

    def refuse_below_floor(self, *, store: str, current: str | None = None) -> None:
        return None

    def knows_revision(self, revision: str) -> bool:
        return True

    def reaches(self, revision: str | None, floor: str) -> bool:
        return True

    def upgrade(self, revision: str = "head") -> None:
        return None


def _migrations(url: str) -> MigrationRunner:
    return FakeMigrations(script_location=Path("migrations"), url=url)


def _nothing_answers(_socket: Path) -> str | None:
    return None


@dataclass
class FakeHereRecords:
    """This host's records as the test states them: none by default."""

    held_records: HeldRecords = field(default_factory=HeldRecords)

    def held(self) -> HeldRecords:
        return self.held_records


@dataclass
class IngestWorld:
    """One ingest into ``HERE`` over the fakes above, every port journaling into ``journal``."""

    journal: list[tuple[object, ...]]
    files: FakeHostFiles
    sources: FakeSourceHosts
    stores: FakeIngestStores
    host_file: FakeHostFileEditor
    tokens: FakeTokenFiles
    ciphers: FakeHostCiphers
    records: FakeHereRecords = field(default_factory=lambda: FakeHereRecords())

    def ingest(self) -> RunnerIngest:
        return RunnerIngest(
            host=IngestHost(
                root=HERE,
                data_dir=HERE / "data",
                runners_dir=HERE / "runners",
                db_url=HERE_URL,
                hub_url="http://hub.test",
                holds_host_token=True,
                declared=declarations(HERE, ()),
            ),
            sources=self.sources,
            stores=self.stores,
            host_file=self.host_file,
            tokens=self.tokens,
            ciphers=self.ciphers,
            hub=FakeHostRecordReader(),
            holder=_nothing_answers,
            files=self.files,
            clock=FixedClock(T0),
            migrations=_migrations,
            records=self.records,
        )


def ingest_world(kinds: Mapping[Path, SourceKind]) -> IngestWorld:
    journal: list[tuple[object, ...]] = []
    return IngestWorld(
        journal=journal,
        files=FakeHostFiles(journal),
        sources=FakeSourceHosts(journal, kinds),
        stores=FakeIngestStores(journal),
        host_file=FakeHostFileEditor(journal),
        tokens=FakeTokenFiles(),
        ciphers=FakeHostCiphers(journal),
    )


_files_conforms: type[IHostFiles] = FakeHostFiles
_sources_conforms: type[ISourceHosts] = FakeSourceHosts
_stores_conforms: type[IIngestStores] = FakeIngestStores
_editor_conforms: type[IHostFileEditor] = FakeHostFileEditor
_tokens_conforms: type[ITokenFiles] = FakeTokenFiles
_cipher_conforms: type[ISecretCipher] = FakeCipher
_ciphers_conforms: type[IHostCiphers] = FakeHostCiphers
