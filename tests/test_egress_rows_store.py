"""Egress rows from hydrated facts (component tier) — content written through the hub's store never reaches a row."""

from __future__ import annotations

from dataclasses import fields
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.hub.domain.egress.rows import InvocationRow, StepRow, UsageRow, step_row
from blizzard.hub.domain.tracing.steps import StepKind, identify_steps
from blizzard.hub.domain.tracing.summary import summarize_step
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.trace_store import TraceStore
from tests import trace_fixtures as fx
from tests.support import HubHarness, hub_store_connections, ingest
from tests.trace_hub import claim, label, pass_build, trace_hub

pytestmark = pytest.mark.component

SENTINELS = {
    "ask": "SENTINEL-ask-text",
    "answer": "SENTINEL-answer-text",
    "check output": "SENTINEL-check-output",
    "chunk title": "SENTINEL-chunk-title",
    "chunk body": "SENTINEL-chunk-body",
    "resolver login": "SENTINEL-resolver-login",
    "envelope": "SENTINEL-bounce-envelope",
    "takeover": "SENTINEL-takeover-command",
    "choice description": "SENTINEL-choice-description",
}


def _plant(hub: HubHarness, chunk_id: str, build_node_id: str) -> None:
    now = hub.clock.now()
    with hub.engine.begin() as conn:
        conn.execute(
            sa.insert(s.work_items).values(
                work_item_id="wi_planted",
                source="default",
                ref="1",
                title=SENTINELS["chunk title"],
                body=SENTINELS["chunk body"],
                author_kind="fleet",
                author_payload="{}",
                created_at=now,
                edited_at=now,
            )
        )
        conn.execute(
            sa.insert(s.questions).values(
                question_id="qn_planted",
                chunk_id=chunk_id,
                node_id=build_node_id,
                runner_id="r1",
                epoch=1,
                question=SENTINELS["ask"],
                options="[]",
                asked_at=now,
            )
        )
        conn.execute(
            sa.insert(s.question_answers).values(
                question_id="qn_planted",
                answer=SENTINELS["answer"],
                answered_by=SENTINELS["resolver login"],
                answered_at=now,
            )
        )
        conn.execute(
            sa.insert(s.chunk_bounces).values(
                chunk_id=chunk_id,
                epoch=1,
                cause="checks",
                envelope=SENTINELS["envelope"] + SENTINELS["check output"],
                recorded_at=now,
            )
        )
        conn.execute(
            sa.insert(s.decisions).values(
                decision_id="dec_planted",
                chunk_id=chunk_id,
                node_id=build_node_id,
                node_name="build",
                epoch=1,
                choices=f'[{{"name": "approve", "description": "{SENTINELS["choice description"]}"}}]',
                submitted_at=now,
            )
        )
        conn.execute(
            sa.insert(s.decision_resolutions).values(
                decision_id="dec_planted", choice="approve", resolved_by=SENTINELS["resolver login"], resolved_at=now
            )
        )
        conn.execute(
            sa.insert(s.usage_facts).values(
                chunk_id=chunk_id,
                node_id=build_node_id,
                epoch=1,
                runner_id="r1",
                kind="spawn",
                model="claude-x",
                input_tokens=1,
                output_tokens=2,
                cache_read_tokens=3,
                cache_create_tokens=4,
                cost_usd=0.5,
                recorded_at=now,
            )
        )
        conn.execute(
            sa.insert(s.escalations).values(
                chunk_id=chunk_id,
                epoch=1,
                takeover_command=SENTINELS["takeover"],
                wrapped_takeover_command=SENTINELS["takeover"],
                decision_id="dec_planted",
                recorded_at=now + timedelta(seconds=1),
            )
        )


def test_planted_content_hydrated_through_the_store_never_reaches_a_row(tmp_path: Path) -> None:
    hub, graph = trace_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    claim(hub, chunk_id, seq=1)
    hub.clock.advance(timedelta(seconds=5))
    build = next(n for n in graph.nodes if n.name == "build")
    _plant(hub, chunk_id, build.node_id)
    hub.clock.advance(timedelta(seconds=5))
    pass_build(hub, chunk_id, graph)

    facts = TraceStore(hub_store_connections(hub.engine), graphs=hub.services.graphs, label=label).step_facts_for(
        [chunk_id]
    )[chunk_id]
    steps = identify_steps(facts)
    closed = [st for st in steps if st.close is not None]
    assert {st.kind for st in closed} == {StepKind.RUNNER, StepKind.GATE}
    rows: list[StepRow | InvocationRow] = [step_row(summarize_step(facts, st, steps), hub.clock.now()) for st in closed]
    rows += [fx.invocation_of(facts, UsageRow(1, chunk_id, "r1", u), hub.clock.now()) for u in facts.usage]

    assert len(facts.usage) == 1
    assert any(isinstance(r, StepRow) and r.asks == 1 for r in rows)
    blob = " ".join(repr(getattr(row, f.name)) for row in rows for f in fields(row))
    assert chunk_id in blob
    leaked = [kind for kind, needle in SENTINELS.items() if needle in blob]
    assert leaked == []
