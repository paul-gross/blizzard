"""The inserted-id read every chunk-row insert returns through (unit tier)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import CursorResult

from blizzard.hub.store.errors import HubStoreError
from blizzard.hub.store.internal.chunk_rows import inserted_id

pytestmark = pytest.mark.unit


def _result(key: tuple[object, ...] | None) -> CursorResult[Any]:
    return cast(CursorResult[Any], SimpleNamespace(inserted_primary_key=key))


def test_the_inserted_key_is_returned() -> None:
    assert inserted_id(_result((7,))) == 7


@pytest.mark.parametrize("key", [None, (None,)], ids=["no-key", "null-key"])
def test_a_missing_inserted_key_raises_instead_of_returning_0(key: tuple[object, ...] | None) -> None:
    with pytest.raises(HubStoreError):
        inserted_id(_result(key))
