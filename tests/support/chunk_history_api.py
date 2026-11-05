"""Shared fixtures for the lease history route tests (``tests/unit/runner/api/test_chunk_history.py``, ``tests/component/runner/api/test_chunk_history.py``)."""

from __future__ import annotations

CHUNK = "ch_1"


# The hub's ``ChunkDetail`` payload: a chunk that bounced once and migrated once, so all
# three row kinds are proven at once, deliberately out of oldest-first source order.
DETAIL: dict[str, object] = {
    "chunk_id": CHUNK,
    "graph_id": "gr_1",
    "status": "running",
    "current_node_id": "nd_build",
    "latest_epoch": 3,
    "history": [
        {
            "from_node_id": "nd_build",
            "from_node_name": "build",
            "to_node_id": "nd_review",
            "to_node_name": "review",
            "choice_name": "ready",
            "epoch": 1,
            "recorded_at": "2026-07-21T10:00:00+00:00",
            "graph_id": "gr_1",
            "graph_name": "adv-dwf",
        },
        {
            "from_node_id": "nd_review",
            "from_node_name": "review",
            "to_node_id": "nd_build",
            "to_node_name": "build",
            "choice_name": "fail",
            "epoch": 2,
            "recorded_at": "2026-07-21T12:00:00+00:00",
            "graph_id": "gr_1",
            "graph_name": "adv-dwf",
        },
    ],
    "migrations": [
        {
            "from_node_id": "nd_triage",
            "from_node_name": "triage",
            "from_graph_id": "gr_0",
            "from_graph_name": "triage-graph",
            "to_graph_id": "gr_1",
            "to_graph_name": "adv-dwf",
            "landed_node_id": "nd_build",
            "landed_node_name": "build",
            "choice_name": "route",
            "source": "authored-edge",
            "epoch": 1,
            "recorded_at": "2026-07-21T09:00:00+00:00",
        }
    ],
    "bounces": [
        {
            "cause": "conflict",
            "envelope": '{"base": "master"}',
            "recorded_at": "2026-07-21T11:00:00+00:00",
        }
    ],
}
