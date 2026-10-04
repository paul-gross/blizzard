"""``build_services`` wiring of the finding bucket reader (component tier): a real,
store-bound reader, not an absent or unbound one."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.domain.finding_bucket import FindingBucketReader
from tests.support import build_hub

pytestmark = pytest.mark.component


def test_the_finding_bucket_is_a_reader_bound_to_the_finding_store(tmp_path: Path) -> None:
    services = build_hub(tmp_path).services

    assert isinstance(services.finding_bucket, FindingBucketReader)
    assert services.finding_bucket._findings is services.findings  # type: ignore[attr-defined]
