"""``canonical_origin`` answers every row of the shared case table (unit tier)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from blizzard.hub.domain.config.forges import OriginRefused, canonical_origin

pytestmark = pytest.mark.unit

_CASES: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "fixtures" / "canonical_origin_cases.json").read_text()
)


@pytest.mark.parametrize("row", _CASES, ids=[row["case"] for row in _CASES])
def test_canonical_origin_answers_the_case_table(row: dict[str, Any]) -> None:
    if "refused" in row:
        with pytest.raises(OriginRefused) as refused:
            canonical_origin(row["input"])
        assert refused.value.kind == row["refused"]
        assert refused.value.reason
    else:
        assert canonical_origin(row["input"]) == row["origin"]
