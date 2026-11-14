"""Builds the hub's work source registry over the stored records.

One credentialed ``httpx2.Client`` per configured source record — never a shared client — on the addresses of the
forge the record names, authenticated by the authorization of the credential the record acts with. The family reveals
nothing: it asks its own :class:`IWorkSourceAuthorizations` for an authorization. A ``provider -> entry`` map selects
the adapter; confined to ``internal/`` (``bzh:dependency-inversion``), keeping ``httpx2`` out of the root.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx2

from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.domain.config.credentials import (
    CredentialUnavailable,
    IReadCredentialRepository,
    ResolvedCredential,
    resolve_credential,
)
from blizzard.hub.domain.config.forges import ConfiguredForge, IReadForgeRepository
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource, IReadWorkSourceRepository
from blizzard.hub.domain.garden.proposals.resolution import GardenProposalDeliveryResolution
from blizzard.hub.domain.kernel.forge_refusals import ForgeMintRefused
from blizzard.hub.domain.work_items.editing import WorkItemEditService
from blizzard.hub.domain.work_items.ports import IReadWorkItemRepository
from blizzard.hub.live_config import ConfigObjectCache
from blizzard.hub.work_sources.internal.github_work_source import GitHubWorkSource
from blizzard.hub.work_sources.internal.hub_work_source import HubWorkSource
from blizzard.hub.work_sources.rate_gate import IWorkSourceRateBudgets, IWorkSourceRateGate
from blizzard.hub.work_sources.refusal_reader import IWorkSourceRefusalReader
from blizzard.hub.work_sources.registry import BuiltWorkSource, StoreWorkSourceRegistry
from blizzard.hub.work_sources.source import IWorkSource, WorkSourceAuthRefused, WorkSourceError


class IWorkSourceAuthorization(Protocol):
    """What a source's client is authenticated with: the ``auth`` on each request, and the ``authority`` it acts as,
    which keys the forge budget it shares with every caller acting as that authority."""

    @property
    def auth(self) -> httpx2.Auth: ...

    @property
    def authority(self) -> str: ...


class IWorkSourceAuthorizations(Protocol):
    """The family's narrow view of the forge authorization boundary, fixed to work-source use."""

    def authorization(self, credential: ResolvedCredential) -> IWorkSourceAuthorization:
        """``credential``'s authorization; :class:`CredentialUnavailable` when it or its secret is unknown or
        retired."""
        ...


@dataclass(frozen=True)
class WorkSourceEntry:
    """One stored source record with the forge it names and the authorization of the credential it acts with —
    ``None`` for a source naming none — resolved to the adapter it names."""

    record: ConfiguredWorkSource
    forge: ConfiguredForge
    authorization: IWorkSourceAuthorization | None
    #: Applied to the source's client as it is built — the composition root's tracing hook.
    instrument: Callable[[httpx2.Client], None] = lambda _client: None
    #: The source's forge key's rate gate; its client's answers teach it, and its adapter admits each call through it.
    gate: IWorkSourceRateGate | None = None
    #: The source's forge kind's refusal reader, classifying a credential refusal its gate declines as a rate one.
    refusals: IWorkSourceRefusalReader | None = None

    @classmethod
    def of(
        cls,
        record: ConfiguredWorkSource,
        forge: ConfiguredForge,
        authorization: IWorkSourceAuthorization | None,
        instrument: Callable[[httpx2.Client], None] | None = None,
        budgets: IWorkSourceRateBudgets | None = None,
        refusals: Callable[[str], IWorkSourceRefusalReader] | None = None,
    ) -> WorkSourceEntry:
        """The entry ``record``'s provider names; with ``budgets``, gated by the budget of the forge it names as the
        authorization's authority — the forge's anonymous budget when it has none; with ``refusals``, reading its
        forge's credential refusals by that forge's kind."""
        kinds: dict[str, type[WorkSourceEntry]] = {"github": GithubEntry}
        kind = kinds.get(record.fields.provider)
        if kind is None:
            raise WorkSourceError(f"work source {record.slug} has unknown provider {record.fields.provider!r}")
        authority = authorization.authority if authorization is not None else None
        gate = budgets.sweeping(record.fields.forge_id, authority, forge.kind) if budgets is not None else None
        reader = refusals(forge.kind) if refusals is not None else None
        if gate is None:
            if instrument is None:
                return kind(record, forge, authorization, refusals=reader)
            return kind(record, forge, authorization, instrument, refusals=reader)

        def learn(response: httpx2.Response) -> None:
            gate.learn(response.headers)

        def teach(client: httpx2.Client) -> None:
            client.event_hooks["response"].append(learn)
            if instrument is not None:
                instrument(client)

        return kind(record, forge, authorization, teach, gate, reader)

    @classmethod
    def built(
        cls,
        record: ConfiguredWorkSource,
        *,
        forges: IReadForgeRepository,
        credentials: IReadCredentialRepository,
        authorizations: IWorkSourceAuthorizations,
        budgets: IWorkSourceRateBudgets,
        instrument: Callable[[httpx2.Client], None] | None = None,
        refusals: Callable[[str], IWorkSourceRefusalReader] | None = None,
    ) -> BuiltWorkSource:
        """Build the record's adapter on its forge's addresses, over a client authenticated by the authorization of
        the credential it acts with — :class:`WorkSourceAuthRefused` when the forge refuses the access that
        authorization is built on."""
        forge = forges.get(record.fields.forge_id)
        if forge is None:
            raise WorkSourceError(f"work source {record.slug} names no forge")
        try:
            credential = resolve_credential(record, credentials)
            authorization = authorizations.authorization(credential) if credential is not None else None
        except CredentialUnavailable as exc:
            raise WorkSourceError(f"work source {record.slug}: {exc}") from exc
        except ForgeMintRefused as exc:
            raise WorkSourceAuthRefused(f"work source {record.slug}", exc.classified) from exc
        except httpx2.HTTPError as exc:
            raise WorkSourceError(f"work source {record.slug}: its forge could not be read: {exc}") from exc
        entry = cls.of(record, forge, authorization, instrument, budgets, refusals)
        client = entry.client()
        return BuiltWorkSource(record=record, source=entry.source(client), release=client.close)

    @classmethod
    def registry(
        cls,
        *,
        records: IReadWorkSourceRepository,
        forges: IReadForgeRepository,
        credentials: IReadCredentialRepository,
        objects: ConfigObjectCache,
        authorizations: IWorkSourceAuthorizations,
        users: IReadUserRepository,
        work_item_store: IReadWorkItemRepository,
        edits: WorkItemEditService,
        resolution: GardenProposalDeliveryResolution,
        budgets: IWorkSourceRateBudgets,
        refusals: Callable[[str], IWorkSourceRefusalReader] | None = None,
        close_forge_writes_enabled: bool = True,
        instrument_client: Callable[[httpx2.Client], None] | None = None,
    ) -> StoreWorkSourceRegistry:
        """Construct a store-backed registry; ``instrument_client`` applies to each
        configured source's client, ``budgets`` gates each one's forge calls, and ``refusals`` reads its forge kind's
        credential refusals."""
        return StoreWorkSourceRegistry(
            records=records,
            objects=objects,
            build=lambda record: cls.built(
                record,
                forges=forges,
                credentials=credentials,
                authorizations=authorizations,
                budgets=budgets,
                instrument=instrument_client,
                refusals=refusals,
            ),
            built_in=HubWorkSource(work_item_store, edits, users, resolution),
            close_forge_writes_enabled=close_forge_writes_enabled,
        )

    def client(self) -> httpx2.Client:
        """A new client on the forge's API, authenticated by the entry's authorization."""
        if self.authorization is None:
            raise WorkSourceError(f"work source {self.record.slug} names no secret")
        client = httpx2.Client(base_url=self.api_base, auth=self.authorization.auth, timeout=30.0)
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
            refusals=self.refusals,
            forge_id=self.forge.forge_id,
        )
