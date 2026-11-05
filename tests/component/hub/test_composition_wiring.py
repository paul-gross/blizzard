"""``build_services`` wiring of the finding bucket reader: a real,
store-bound reader, not an absent or unbound one."""

from __future__ import annotations

from pathlib import Path

from blizzard.hub.domain.garden.findings.bucket import FindingBucketReader
from tests.support.scaffolding import build_hub


def test_the_finding_bucket_is_a_reader_bound_to_the_finding_store(tmp_path: Path) -> None:
    services = build_hub(tmp_path).services

    assert isinstance(services.finding_bucket, FindingBucketReader)
    assert services.finding_bucket.findings is services.findings
