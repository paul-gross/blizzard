"""Withdrawal and re-ingest decide against the same pointer state."""

from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest

from blizzard.foundation.work_items import WorkItemClosure
from blizzard.hub.domain.chunk.ingest import IngestService, IngestWithdrawnItem
from blizzard.hub.domain.chunk.model import WorkItemAuthor
from tests.support import seed_work_item
from tests.test_hub_work_source import _T0, _graph, _source

pytestmark = pytest.mark.component


@pytest.mark.parametrize("withdraw_first", [True, False])
def test_withdraw_and_ingest_in_both_orders(tmp_path: Path, withdraw_first: bool) -> None:
    source, items, chunks, _, engine, clock = _source(tmp_path)
    graph = _graph(engine)
    item = seed_work_item(items, graph_id=graph.graph_id, author=WorkItemAuthor.user("u"), at=_T0)
    pointer = item.pointer
    with chunks.exclusive.locked([f"ch_{item.ref}"]) as handle:
        chunks.lifecycle.record_stop_locked(handle, f"ch_{item.ref}", by="test", at=_T0)
    ingest = IngestService(
        record=chunks.record,
        exclusive=chunks.exclusive,
        links=cast(Any, Mock()),
        items=items,
        labels=cast(Any, Mock()),
        clock=clock,
    )
    assert item.project_id is not None
    if withdraw_first:
        source.withdraw(pointer, by="test")
        with pytest.raises(IngestWithdrawnItem):
            ingest.ingest([pointer], graph=graph, project=item.project_id)
        assert chunks.work_refs.find_live_holder(pointer) is None
    else:
        holder = ingest.ingest([pointer], graph=graph, project=item.project_id)
        result = source.withdraw(pointer, by="test")
        assert result.deleted_chunk_id == holder
        assert chunks.record.get(holder) is None
    current = items.get(pointer.source, pointer.ref)
    assert current is not None and current.closure == WorkItemClosure.WITHDRAWN
