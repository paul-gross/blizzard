"""Builds the hub's work source registry over the stored records.

One credentialed ``httpx.Client`` per configured source record — never a shared client,
never a shared token. A ``provider -> entry`` map selects the adapter; confined to
``internal/`` (``bzh:dependency-inversion``), keeping ``httpx`` out of the root.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx

from blizzard.foundation.roles import collaborator
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.domain.chunk.model import IReadWorkItemRepository
from blizzard.hub.domain.config.secrets import ISecretReader, SecretName, SecretNotFound, SecretRetired, SecretValue
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource, IReadWorkSourceRepository
from blizzard.hub.domain.garden.proposals.resolution import GardenProposalDeliveryResolution
from blizzard.hub.domain.work_items.editing import WorkItemEditService
from blizzard.hub.forge.web import web_origin
from blizzard.hub.live_config import ConfigObjectCache
from blizzard.hub.work_sources.internal.github_work_source import GitHubWorkSource
from blizzard.hub.work_sources.internal.hub_work_source import HubWorkSource
from blizzard.hub.work_sources.registry import BuiltWorkSource, StoreWorkSourceRegistry
from blizzard.hub.work_sources.source import IWorkSource, WorkSourceError


@collaborator
@dataclass(frozen=True)
class WorkSourceEntry:
    """One stored source record with its revealed credential, resolved to the adapter it names."""

    record: ConfiguredWorkSource
    secret: SecretValue | None
    #: Applied to the source's client as it is built — the composition root's tracing hook.
    instrument: Callable[[httpx.Client], None] = lambda _client: None

    @classmethod
    def of(
        cls,
        record: ConfiguredWorkSource,
        secret: SecretValue | None,
        instrument: Callable[[httpx.Client], None] | None = None,
    ) -> WorkSourceEntry:
        kinds: dict[str, type[WorkSourceEntry]] = {"github": GithubEntry}
        kind = kinds.get(record.fields.provider)
        if kind is None:
            raise WorkSourceError(f"work source {record.slug} has unknown provider {record.fields.provider!r}")
        return kind(record, secret) if instrument is None else kind(record, secret, instrument)

    @classmethod
    def built(
        cls,
        record: ConfiguredWorkSource,
        *,
        secrets: ISecretReader,
        instrument: Callable[[httpx.Client], None] | None = None,
    ) -> BuiltWorkSource:
        """Build the record's adapter over a client carrying its revealed secret."""
        secret: SecretValue | None = None
        if record.fields.secret is not None:
            try:
                secret = secrets.reveal(SecretName.parse(record.fields.secret))
            except (SecretNotFound, SecretRetired) as exc:
                raise WorkSourceError(f"work source {record.slug}: {exc}") from exc
        entry = cls.of(record, secret, instrument)
        client = entry.client
        return BuiltWorkSource(record=record, source=entry.source(client), release=client.close)

    @classmethod
    def registry(
        cls,
        *,
        records: IReadWorkSourceRepository,
        objects: ConfigObjectCache,
        secrets: ISecretReader,
        users: IReadUserRepository,
        work_item_store: IReadWorkItemRepository,
        edits: WorkItemEditService,
        resolution: GardenProposalDeliveryResolution,
        close_forge_writes_enabled: bool = True,
        instrument_client: Callable[[httpx.Client], None] | None = None,
    ) -> StoreWorkSourceRegistry:
        """Construct a store-backed registry; ``instrument_client`` applies to each
        configured source's client."""
        return StoreWorkSourceRegistry(
            records=records,
            objects=objects,
            build=lambda record: cls.built(record, secrets=secrets, instrument=instrument_client),
            built_in=HubWorkSource(work_item_store, edits, users, resolution),
            close_forge_writes_enabled=close_forge_writes_enabled,
        )

    @property
    def token(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        if self.secret is None:
            raise WorkSourceError(f"work source {self.record.slug} names no secret")
        return self.secret.expose()

    @property
    def client(self) -> httpx.Client:
        headers = {"Authorization": f"token {self.token}"}
        client = httpx.Client(base_url=self.api_base, headers=headers, timeout=30.0)
        self.instrument(client)
        return client

    @property
    def api_base(self) -> str:
        raise NotImplementedError

    def source(self, client: httpx.Client) -> IWorkSource:
        raise NotImplementedError


class GithubEntry(WorkSourceEntry):
    DEFAULT_API_BASE = "https://api.github.com"

    @property
    def api_base(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        return self.record.fields.api_base or self.DEFAULT_API_BASE

    @property
    def web_base(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        """The record's own web origin, else the one its API base implies."""
        return self.record.fields.web_base or web_origin(self.api_base)

    def source(self, client: httpx.Client) -> IWorkSource:
        return GitHubWorkSource(
            client,
            source_id=self.record.work_source_id,
            slug=self.record.slug,
            repo=self.record.fields.locator,
            web_base=self.web_base,
        )
