"""Builds the hub's work source registry over the stored records.

One credentialed ``httpx2.Client`` per configured source record — never a shared client,
never a shared token — on the addresses of the forge the record names. A ``provider -> entry``
map selects the adapter; confined to ``internal/`` (``bzh:dependency-inversion``), keeping ``httpx2`` out of the root.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx2

from blizzard.foundation.roles import collaborator
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.domain.chunk.model import IReadWorkItemRepository
from blizzard.hub.domain.config.forges import ConfiguredForge, IReadForgeRepository
from blizzard.hub.domain.config.secrets import ISecretReader, SecretName, SecretNotFound, SecretRetired, SecretValue
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource, IReadWorkSourceRepository
from blizzard.hub.domain.garden.proposals.resolution import GardenProposalDeliveryResolution
from blizzard.hub.domain.work_items.editing import WorkItemEditService
from blizzard.hub.live_config import ConfigObjectCache
from blizzard.hub.work_sources.internal.github_work_source import GitHubWorkSource
from blizzard.hub.work_sources.internal.hub_work_source import HubWorkSource
from blizzard.hub.work_sources.rate_gate import IWorkSourceRateBudgets, IWorkSourceRateGate
from blizzard.hub.work_sources.registry import BuiltWorkSource, StoreWorkSourceRegistry
from blizzard.hub.work_sources.source import IWorkSource, WorkSourceError


@collaborator
@dataclass(frozen=True)
class WorkSourceEntry:
    """One stored source record with the forge it names and its revealed credential, resolved to the adapter it
    names."""

    record: ConfiguredWorkSource
    forge: ConfiguredForge
    secret: SecretValue | None
    #: Applied to the source's client as it is built — the composition root's tracing hook.
    instrument: Callable[[httpx2.Client], None] = lambda _client: None
    #: The source's forge key's rate gate; its client's answers teach it, and its adapter admits each call through it.
    gate: IWorkSourceRateGate | None = None

    @classmethod
    def of(
        cls,
        record: ConfiguredWorkSource,
        forge: ConfiguredForge,
        secret: SecretValue | None,
        instrument: Callable[[httpx2.Client], None] | None = None,
        budgets: IWorkSourceRateBudgets | None = None,
    ) -> WorkSourceEntry:
        """The entry ``record``'s provider names; with ``budgets``, gated by the budget of the forge it names under the
        secret it names — the forge's anonymous budget when it names none."""
        kinds: dict[str, type[WorkSourceEntry]] = {"github": GithubEntry}
        kind = kinds.get(record.fields.provider)
        if kind is None:
            raise WorkSourceError(f"work source {record.slug} has unknown provider {record.fields.provider!r}")
        gate = budgets.sweeping(record.fields.forge_id, record.fields.secret) if budgets is not None else None
        if gate is None:
            return kind(record, forge, secret) if instrument is None else kind(record, forge, secret, instrument)

        def learn(response: httpx2.Response) -> None:
            gate.learn(response.headers)

        def teach(client: httpx2.Client) -> None:
            client.event_hooks["response"].append(learn)
            if instrument is not None:
                instrument(client)

        return kind(record, forge, secret, teach, gate)

    @classmethod
    def built(
        cls,
        record: ConfiguredWorkSource,
        *,
        forges: IReadForgeRepository,
        secrets: ISecretReader,
        budgets: IWorkSourceRateBudgets,
        instrument: Callable[[httpx2.Client], None] | None = None,
    ) -> BuiltWorkSource:
        """Build the record's adapter on its forge's addresses, over a client carrying its revealed secret."""
        forge = forges.get(record.fields.forge_id)
        if forge is None:
            raise WorkSourceError(f"work source {record.slug} names no forge")
        secret: SecretValue | None = None
        if record.fields.secret is not None:
            try:
                secret = secrets.reveal(SecretName.parse(record.fields.secret))
            except (SecretNotFound, SecretRetired) as exc:
                raise WorkSourceError(f"work source {record.slug}: {exc}") from exc
        entry = cls.of(record, forge, secret, instrument, budgets)
        client = entry.client
        return BuiltWorkSource(record=record, source=entry.source(client), release=client.close)

    @classmethod
    def registry(
        cls,
        *,
        records: IReadWorkSourceRepository,
        forges: IReadForgeRepository,
        objects: ConfigObjectCache,
        secrets: ISecretReader,
        users: IReadUserRepository,
        work_item_store: IReadWorkItemRepository,
        edits: WorkItemEditService,
        resolution: GardenProposalDeliveryResolution,
        budgets: IWorkSourceRateBudgets,
        close_forge_writes_enabled: bool = True,
        instrument_client: Callable[[httpx2.Client], None] | None = None,
    ) -> StoreWorkSourceRegistry:
        """Construct a store-backed registry; ``instrument_client`` applies to each
        configured source's client, and ``budgets`` gates each one's forge calls."""
        return StoreWorkSourceRegistry(
            records=records,
            objects=objects,
            build=lambda record: cls.built(
                record, forges=forges, secrets=secrets, budgets=budgets, instrument=instrument_client
            ),
            built_in=HubWorkSource(work_item_store, edits, users, resolution),
            close_forge_writes_enabled=close_forge_writes_enabled,
        )

    @property
    def token(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        if self.secret is None:
            raise WorkSourceError(f"work source {self.record.slug} names no secret")
        return self.secret.expose()

    @property
    def client(self) -> httpx2.Client:
        headers = {"Authorization": f"token {self.token}"}
        client = httpx2.Client(base_url=self.api_base, headers=headers, timeout=30.0)
        self.instrument(client)
        return client

    @property
    def api_base(self) -> str:
        raise NotImplementedError

    def source(self, client: httpx2.Client) -> IWorkSource:
        raise NotImplementedError


class GithubEntry(WorkSourceEntry):
    @property
    def api_base(self) -> str:
        return self.forge.fields.api_base

    @property
    def web_base(self) -> str:
        """The forge's effective web origin."""
        return self.forge.web_origin

    def source(self, client: httpx2.Client) -> IWorkSource:
        return GitHubWorkSource(
            client,
            source_id=self.record.work_source_id,
            slug=self.record.slug,
            repo=self.record.fields.locator,
            web_base=self.web_base,
            gate=self.gate,
        )
