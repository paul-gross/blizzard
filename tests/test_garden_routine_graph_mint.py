"""The packaged garden-routine graph mints clean through the real ``/api/graphs`` route
(component tier), and its ``no-strategy`` choice resolves to a real edge — the machinery
`test_garden_routine_graph.py`'s unit tier pins over the loaded doc, proven here over
what a mint actually reifies."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.graphs import PACKAGED
from tests.support import build_hub

pytestmark = pytest.mark.component


def test_garden_routine_mints_clean_and_no_strategy_resolves_to_reconcile(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    definition_yaml = PACKAGED.named("garden-routine").inlined_yaml
    minted = hub.client.post("/api/graphs", json={"definition_yaml": definition_yaml})
    assert minted.status_code == 201, minted.text
    body = minted.json()

    survey = next(n for n in body["nodes"] if n["name"] == "survey")
    no_strategy = next(c for c in survey["choices"] if c["name"] == "no-strategy")

    edge = next(e for e in body["edges"] if e["choice_id"] == no_strategy["choice_id"])
    assert edge["to_node_name"] == "reconcile"
