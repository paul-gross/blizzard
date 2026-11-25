"""The tenant scope's wiring of the finding bucket reader: a real,
store-bound reader, not an absent or unbound one."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from blizzard.hub.delivery.action_registry import HubActions
from blizzard.hub.domain.garden.findings.bucket import FindingBucketReader
from blizzard.hub.domain.garden.findings.model import IReadFindingRepository
from blizzard.hub.domain.observability.analytics.derivation import EventDerivationService
from blizzard.hub.domain.observability.analytics.extraction import FileReadExtractor
from blizzard.hub.domain.tenancy.administration import TenantAdministration
from blizzard.hub.store import schema
from blizzard.hub.tenancy import HUB_SCOPED_OPTION
from tests.support.scaffolding import HubHarness, build_hub


def test_the_finding_bucket_is_a_reader_bound_to_the_finding_store(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    bucket = hub.default_adapter(FindingBucketReader)

    assert isinstance(bucket, FindingBucketReader)
    assert bucket.findings is hub.default_adapter(IReadFindingRepository)


def _graph_count(hub: HubHarness) -> int:
    with hub.engine.connect() as conn:
        conn = conn.execution_options(**{HUB_SCOPED_OPTION: True})
        return conn.execute(sa.select(sa.func.count()).select_from(schema.graphs)).scalar_one()


def test_a_new_tenant_is_minted_against_the_actions_the_hub_was_built_with(tmp_path: Path) -> None:
    (tmp_path / "served").mkdir()
    (tmp_path / "bare").mkdir()
    served = build_hub(tmp_path / "served")
    bare = build_hub(tmp_path / "bare", actions=HubActions.of([]))
    served_before, bare_before = _graph_count(served), _graph_count(bare)

    served.resolve_hub(TenantAdministration).create("acme", admin_user_id=None, by="usr_creator")
    bare.resolve_hub(TenantAdministration).create("acme", admin_user_id=None, by="usr_creator")

    # A catalogue naming no actions refuses each packaged graph that names one; only the injected catalogue can say so.
    assert _graph_count(served) - served_before > _graph_count(bare) - bare_before


def test_the_event_derivation_service_derives_with_the_extractors_the_hub_was_built_with(tmp_path: Path) -> None:
    extractors = (FileReadExtractor(),)

    hub = build_hub(tmp_path, extractors=extractors)

    assert list(hub.default_adapter(EventDerivationService)._extractors) == list(extractors)  # pyright: ignore[reportPrivateUsage]  # the wiring is the observation
