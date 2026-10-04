"""Who may read back a lease's stored transcript segments, pinned by value."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.observability.transcripts import LeaseSegmentsNotOwned, refuse_foreign_lease_read

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("owner", ["runner-a", None])
def test_the_shipping_runner_or_an_empty_lease_reads_back(owner: str | None) -> None:
    refuse_foreign_lease_read(owner, requesting_runner_id="runner-a")


def test_another_runners_segments_are_refused() -> None:
    with pytest.raises(LeaseSegmentsNotOwned, match="lease segments belong to another runner") as refused:
        refuse_foreign_lease_read("runner-b", requesting_runner_id="runner-a")
    assert (refused.value.owning_runner_id, refused.value.requesting_runner_id) == ("runner-b", "runner-a")
