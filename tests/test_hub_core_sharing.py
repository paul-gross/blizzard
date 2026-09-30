"""``build_hub_core``'s members are the very instances every service and the work-source
registry share (component tier) — identity, not equality, so no second copy hides behind
an equal-looking one."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import build_hub

pytestmark = pytest.mark.component


def test_services_share_the_cores_stores(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    services = hub.services

    assert services.findings is services.finding_exit._repo
    assert services.garden_proposals is services.garden_proposal_authoring._proposals
    assert services.garden_proposal_closures is services.garden_proposal_authoring._closures
    assert services.delete is services.work_item_materialization._edits._delete
    assert services.registry is services.claim._registry
    assert services.registry is services.fleet._registry
    assert services.registry is services.claim._exclusive._registry  # type: ignore[attr-defined]


def test_the_materialization_reconciler_and_the_hub_source_share_one_edit_service(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    hub_editor = hub.work_sources.editor("hub")
    assert hub_editor is not None

    assert hub_editor._edits is hub.services.work_item_materialization._edits  # type: ignore[attr-defined]
    assert hub_editor._edits is hub.services.garden_proposal_closure._items  # type: ignore[attr-defined]
