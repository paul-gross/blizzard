"""The garden materialization registrations as the hub container holds them: within a tenant the delivery and review
recorders and materializers hold the tenant's canonical collaborators and the flow executor holds the recorders;
across tenants the instances differ (component tier)."""

from __future__ import annotations

from pathlib import Path

from blizzard.hub.api.garden_formats import GardenFormats
from blizzard.hub.delivery.flow_turn import TurnRunner
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.garden.delivery.materialize import GardenDelivery, IWriteGardenDeliveryRepository
from blizzard.hub.domain.garden.delivery.service import GardenDeliveryRecorder
from blizzard.hub.domain.garden.delivery.validation import IProjectCommitResolvers
from blizzard.hub.domain.garden.findings.bucket import FindingBucketReader
from blizzard.hub.domain.garden.findings.model import IWriteFindingRepository, IWriteFindingSetRepository
from blizzard.hub.domain.garden.formats import IGardenFormats
from blizzard.hub.domain.garden.proposals.model import IWriteGardenProposalRepository
from blizzard.hub.domain.garden.review.materialize import IWriteReviewFindingsRepository, ReviewFindingsMaterialize
from blizzard.hub.domain.garden.review.service import ReviewFindingsRecorder
from blizzard.hub.domain.garden.scopes import IWriteScopeRepository
from blizzard.hub.domain.kernel.transactions import IHubTransactions
from blizzard.hub.forge.commit_check import SeamCommitResolver
from blizzard.hub.store.internal.garden_delivery_store import GardenDeliveryStore
from blizzard.hub.store.internal.review_findings_store import ReviewFindingsStore
from blizzard.hub.tenancy import StoreScope
from tests.support.scaffolding import HubHarness, add_tenant, build_hub, default_tenant

# The services hold what they were built over privately; reading it is the only way to see it is shared.
# pyright: reportPrivateUsage=false


def _shared(hub: HubHarness, tenant: StoreScope) -> dict[str, list[object]]:
    """Every holder of each materialization collaborator in ``tenant``, by what the instance is."""
    scope = hub.hub_services.process.tenants.open(tenant).resolver
    delivery = scope.get(GardenDeliveryRecorder)
    review = scope.get(ReviewFindingsRecorder)
    delivering = scope.get(GardenDelivery)
    reviewing = scope.get(ReviewFindingsMaterialize)
    return {
        "delivery store": [
            scope.get(GardenDeliveryStore),
            scope.get(IWriteGardenDeliveryRepository),
            delivering._delivery,
        ],
        "review store": [
            scope.get(ReviewFindingsStore),
            scope.get(IWriteReviewFindingsRepository),
            reviewing._delivery,
        ],
        "findings": [scope.get(IWriteFindingRepository), delivering._findings, reviewing._findings],
        "finding sets": [scope.get(IWriteFindingSetRepository), delivering._finding_sets],
        "proposals": [scope.get(IWriteGardenProposalRepository), delivering._proposals],
        "scopes": [scope.get(IWriteScopeRepository), reviewing._scopes],
        "artifacts": [scope.get(IReadChunkArtifactsRepository), delivery._artifacts, review._artifacts],
        "facts": [scope.get(IReadChunkFactsRepository), delivering._facts, reviewing._facts],
        "transactions": [scope.get(IHubTransactions), delivering._transactions, reviewing._transactions],
        "buckets": [scope.get(FindingBucketReader), delivery._buckets],
        "commit resolvers": [
            scope.get(SeamCommitResolver),
            scope.get(IProjectCommitResolvers),
            delivery._commit_resolvers,
        ],
        "delivery materialize": [delivering, delivery._materialize],
        "review materialize": [reviewing, review._materialize],
        "delivery recorder": [delivery],
        "review recorder": [review],
        "formats": [
            scope.get(GardenFormats),
            scope.get(IGardenFormats),
            delivery._formats,
            review._formats,
        ],
    }


def test_each_materialization_collaborator_is_one_instance_within_a_tenant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    for name, held in _shared(hub, StoreScope(default_tenant(hub.engine))).items():
        assert all(each is held[0] for each in held), name


def test_the_materialization_instances_differ_across_tenants_but_the_formats(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = _shared(hub, StoreScope(default_tenant(hub.engine)))
    second_tenant = StoreScope(add_tenant(hub.engine, "second"))
    hub.hub_services.tenants.open_tenants.refresh()
    second = _shared(hub, second_tenant)

    shared_by_the_process = {name for name, held in first.items() if held[0] is second[name][0]}

    assert shared_by_the_process == {"formats"}


def test_the_flow_executor_holds_the_containers_recorders(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    assert hub.default_adapter(TurnRunner)._garden_delivery is hub.default_adapter(GardenDeliveryRecorder)
    assert hub.default_adapter(TurnRunner)._review_findings is hub.default_adapter(ReviewFindingsRecorder)
