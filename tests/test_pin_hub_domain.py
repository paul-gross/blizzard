"""Hub-domain signature pins (unit tier) — required arguments that carry no default.

Two hub-domain callables deliberately make an argument required with no default, so a
caller that forgets it gets a ``TypeError`` instead of a silently wrong answer — a
reversion no other test would catch, since adding a default keeps every call site green."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.envelope import build_node_envelope
from blizzard.hub.domain.graph import Executor, JudgedBy, Node, SessionMode, resolve_follow_latest
from blizzard.hub.domain.work import Chunk, WorkRef

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 7, 13, tzinfo=UTC)


def _chunk() -> Chunk:
    return Chunk(
        chunk_id="ch_1",
        graph_id="gr_1",
        work_refs=[WorkRef(source="default", ref="1")],
        minted_at=_T0,
    )


def _node() -> Node:
    return Node(
        node_id="nd_build",
        graph_id="gr_1",
        name="build",
        executor=Executor.RUNNER,
        prompt="do the work",
        checks=[],
        produces=[],
        session=SessionMode.RESUME,
        judged_by=JudgedBy.WORKER,
        retries_max=2,
        retries_exhausted="escalate",
        mode=None,
        judgement_prompt=None,
        choices=[],
    )


def test_build_node_envelope_requires_graph_explicitly() -> None:
    """``graph`` carries no default (issue #144): a caller that forgets it gets a
    ``TypeError``, never a silent fall-back to the pre-#144 "no declaration, no chunk
    default" envelope."""
    with pytest.raises(TypeError):
        build_node_envelope(  # type: ignore[call-arg]
            chunk=_chunk(),
            node=_node(),
            artifacts=[],
            epoch=1,
        )


def test_resolve_follow_latest_requires_hub_default_explicitly() -> None:
    """``hub_default`` carries no default of its own (issue #164): a caller that forgets
    the hub setting gets a ``TypeError``, never a silent ``True`` (migrating a fleet that
    never opted in) or a silent ``False`` (never migrating anything)."""
    with pytest.raises(TypeError):
        resolve_follow_latest(None)  # type: ignore[call-arg]
