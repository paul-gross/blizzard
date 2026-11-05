"""Helpers for the invariant checker tests, shared with other test modules."""

from __future__ import annotations

from pathlib import Path

import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as hub
from tests.support.config_inputs import init_hub
from tests.support.migrations.invariant_checker import NOW, insert
from tests.support.scaffolding import (
    hub_scoped,
    stamp,
)


def _hub_engine(tmp_path: Path) -> sa.Engine:  # pyright: ignore[reportUnusedFunction]  # imported by the test modules that share it
    return create_engine_from_url(init_hub(tmp_path / "hub").db_url)


def _forge_repository(  # pyright: ignore[reportUnusedFunction]  # imported by the test modules that share it
    conn: sa.Connection, repository_id: str, *, tenant: str, forge_id: str | None, forge_api_url: str
) -> None:
    conn.execute(
        hub_scoped(insert(hub.repositories)).values(
            tenant_id=tenant,
            repository_id=repository_id,
            slug=repository_id,
            name=repository_id,
            forge_api_url=forge_api_url,
            owner="acme",
            repo=repository_id,
            base_branch="main",
            secret_name="tok",
            revision=1,
            created_at=NOW,
            created_by="test",
            forge_id=forge_id,
        )
    )


def _seed_at_deliver(conn: sa.Connection, chunk_id: str = "ch_1") -> None:  # pyright: ignore[reportUnusedFunction]  # imported by the test modules that share it
    """A chunk whose newest movement entered ``nd_deliver`` at epoch 1."""
    conn.execute(
        insert(hub.chunks).values(stamp(hub.chunks, chunk_id=chunk_id, graph_id="gr_1", minted_at=NOW, model="m"))
    )
    conn.execute(
        insert(hub.lease_facts).values(stamp(hub.lease_facts, chunk_id=chunk_id, epoch=1, runner_id="r", minted_at=NOW))
    )
    conn.execute(
        insert(hub.transitions).values(
            stamp(
                hub.transitions,
                transition_id=f"tr_{chunk_id}",
                chunk_id=chunk_id,
                graph_id="gr_1",
                from_node_id="nd_build",
                to_node_id="nd_deliver",
                choice_name="pass",
                epoch=1,
                runner_id="r",
                recorded_at=NOW,
            )
        )
    )


def _submit(  # pyright: ignore[reportUnusedFunction]  # imported by the test modules that share it
    conn: sa.Connection, submission_id: str, *, chunk_id: str, node_id: str, arrival: int, flow_id: str
) -> None:
    conn.execute(
        insert(hub.flow_submissions).values(
            stamp(
                hub.flow_submissions,
                submission_id=submission_id,
                chunk_id=chunk_id,
                node_id=node_id,
                epoch=1,
                generation=0,
                arrival=arrival,
                submitted_at=NOW,
            )
        )
    )
    conn.execute(
        insert(hub.flow_submission_flows).values(
            stamp(hub.flow_submission_flows, submission_id=submission_id, flow_id=flow_id)
        )
    )


def _flow(conn: sa.Connection, flow_id: str = "flw_main") -> None:  # pyright: ignore[reportUnusedFunction]  # imported by the test modules that share it
    conn.execute(
        insert(hub.flows).values(
            stamp(
                hub.flows,
                flow_id=flow_id,
                resource="repo:repo_a/main",
                repository_id="repo_a",
                branch="main",
                created_at=NOW,
            )
        )
    )


def _open_turn(conn: sa.Connection, turn_id: str, submission_id: str) -> None:  # pyright: ignore[reportUnusedFunction]  # imported by the test modules that share it
    conn.execute(
        insert(hub.hub_turns).values(stamp(hub.hub_turns, turn_id=turn_id, submission_id=submission_id, started_at=NOW))
    )


def _link(conn: sa.Connection, project_id: str, repository_id: str, *, tenant: str) -> None:  # pyright: ignore[reportUnusedFunction]  # imported by the test modules that share it
    conn.execute(
        insert(hub.project_repository_links).values(
            tenant_id=tenant, project_id=project_id, repository_id=repository_id, revision=1
        )
    )


def _terms(conn: sa.Connection, submission_id: str, repository_id: str) -> None:  # pyright: ignore[reportUnusedFunction]  # imported by the test modules that share it
    conn.execute(
        insert(hub.flow_submission_terms).values(
            stamp(
                hub.flow_submission_terms,
                submission_id=submission_id,
                repository_id=repository_id,
                landing_policy="merge-on-checks",
            )
        )
    )
