"""Helpers for the flow routes tests, shared with other test modules."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from blizzard.hub.domain.delivery.flows import Submission
from blizzard.hub.store.internal.flow_store import FlowStore
from tests.support.flow_driver_world import (
    World,
)
from tests.support.scaffolding import hub_store_connections


class Line:
    """Two chunks in one line at ``merge``, the first arrived first, each past its first ``pending`` turn."""

    def __init__(self, tmp_path: Path) -> None:
        self.world = World(tmp_path)
        self.client = self.world.hub.client
        self.first = self.world.at_merge(ref="1")
        self.world.hub.clock.advance(timedelta(seconds=1))
        self.second = self.world.at_merge(ref="2")
        self.world.hub.settle_turns()
        [self.flow_id] = [flow["flow_id"] for flow in self.client.get("/api/flows").json()["flows"]]

    def submission(self, chunk_id: str) -> Submission:
        [sub] = self.world.submissions(chunk_id)
        return sub

    def line(self) -> list[tuple[str, str, int]]:
        body = self.client.get(f"/api/flows/{self.flow_id}").json()
        return [(place["chunk_id"], place["state"], place["position"]) for place in body["line"]]

    def chunk_places(self, chunk_id: str) -> list[tuple[str, str, int]]:
        """The chunk's places as the chunk list and the chunk detail answer them — asserted equal."""
        rows = {row["chunk_id"]: row for row in self.client.get("/api/chunks").json()["chunks"]}
        listed = [(p["flow_id"], p["state"], p["position"]) for p in rows[chunk_id]["flow_places"]]
        detail = [(p["flow_id"], p["state"], p["position"]) for p in self.world.detail(chunk_id)["flow_places"]]
        assert listed == detail
        return listed

    def take_turn(self, chunk_id: str) -> None:
        store = FlowStore(hub_store_connections(self.world.hub.engine), self.world.hub.clock)
        turn = store.take_turn(
            self.submission(chunk_id).submission_id, passed_over=frozenset(), terms=(), at=self.world.hub.clock.now()
        )
        assert turn is not None
