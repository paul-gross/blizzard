"""Wire-parity service tests (paul-gross/blizzard-mock#4) — the real ``HttpHubClient``
driven directly against the mock hub's wire, plus the one behavioral proof that needs
the real runner loop.

``test_parity_guard.py`` proves every ``IHubClient`` endpoint is *served*
(openapi-shape-only); these tests prove three of them are *wire-compatible*, driving
the actual ``HttpHubClient`` binding (``blizzard.runner.loop.internal.http_hub``) the
real runner uses, and one behavioral outcome the mock's ``chunk_unknown`` lever exists
to exercise (research-blizzard.md §4, commit ``68238d0``, blizzard#9):

* dedicated ``report_lease`` / ``report_escalation`` — the direct, non-buffered fact
  routes (``POST /chunks/{id}/leases`` / ``.../escalations``), previously 404 on the
  mock;
* the ask/answer rendezvous — ``push_facts`` minting a ``question.asked`` fact,
  ``get_question`` polling it (previously 404), answered through the mock's own
  ``/_seed/answer`` test-control route;
* the **real runner loop**, driven one ``run_single_tick`` at a time, releasing a held
  environment when the mock hub's ``chunk_unknown`` lever reports its chunk 404 —
  the exercise commit ``68238d0`` exists for.
"""

from __future__ import annotations

import os
import time
import uuid
from pathlib import Path

import pytest

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop.build import run_single_tick
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from blizzard.wire.facts import QUESTION_ASKED, RunnerFact, RunnerFactBatch
from tests.e2e.test_acceptance_loop import REPO, _free_port, _runner_config
from tests.service.support import (
    http_hub_client,
    mint_fixture,
    mock_hub,
    mock_hub_chunk_spec,
    poll_until,
    require_mock_fleet,
    require_winter_source,
    service_gate,
)

pytestmark = [pytest.mark.service, service_gate]

_PM_URL = f"{REPO}/issues/1"


def _seed(hub) -> str:
    resp = hub.post("/_seed/chunk", json=mock_hub_chunk_spec(_PM_URL))
    assert resp.status_code == 201, resp.text
    return resp.json()["chunk_id"]


# --------------------------------------------------------------------------------- #
# 1. Dedicated lease report
# --------------------------------------------------------------------------------- #


def test_report_lease_advances_the_mock_hubs_fence() -> None:
    bin_dir = require_mock_fleet()
    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub, http_hub_client(hub_port) as client:
        chunk_id = _seed(hub)
        before = hub.get(f"/api/fleet/chunks/{chunk_id}")
        assert before.status_code == 200, before.text
        assert before.json()["latest_epoch"] is None  # a freshly seeded chunk fences at 0

        client.report_lease(chunk_id, epoch=5, runner_id="runner-parity")

        after = hub.get(f"/api/fleet/chunks/{chunk_id}")
        assert after.status_code == 200, after.text
        assert after.json()["latest_epoch"] == 5, after.text


# --------------------------------------------------------------------------------- #
# 2. Escalation reporting
# --------------------------------------------------------------------------------- #


def test_report_escalation_lands_on_the_chunk_detail() -> None:
    bin_dir = require_mock_fleet()
    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub, http_hub_client(hub_port) as client:
        chunk_id = _seed(hub)

        client.report_escalation(chunk_id, epoch=3, runner_id="runner-parity", takeover_command="take over")

        detail = hub.get(f"/api/fleet/chunks/{chunk_id}")
        assert detail.status_code == 200, detail.text
        escalation = detail.json()["escalation"]
        assert escalation is not None, detail.text
        assert escalation["epoch"] == 3
        assert escalation["takeover_command"] == "take over"


# --------------------------------------------------------------------------------- #
# 3. Ask/answer through the mock hub
# --------------------------------------------------------------------------------- #


def test_question_ask_answer_round_trips_through_the_mock_hub() -> None:
    bin_dir = require_mock_fleet()
    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub, http_hub_client(hub_port) as client:
        chunk_id = _seed(hub)
        question_id = f"parity-question-{uuid.uuid4().hex[:24]}"

        ack = client.push_facts(
            RunnerFactBatch(
                runner_id="runner-parity",
                facts=[
                    RunnerFact(
                        seq=1,
                        kind=QUESTION_ASKED,
                        payload={
                            "question_id": question_id,
                            "chunk_id": chunk_id,
                            "runner_id": "runner-parity",
                            "epoch": 1,
                            "question": "which way?",
                            "options": ["a", "b"],
                            "asked_at": "2026-07-21T00:00:00+00:00",
                        },
                    )
                ],
            )
        )
        assert 1 in ack.applied, ack

        polled = client.get_question(question_id)
        assert polled.question_id == question_id
        assert polled.answered is False, polled

        answered = hub.post("/_seed/answer", json={"question_id": question_id, "answer": "a"})
        assert answered.status_code == 200, answered.text

        polled_again = client.get_question(question_id)
        assert polled_again.answered is True, polled_again
        assert polled_again.answer == "a"


# --------------------------------------------------------------------------------- #
# 4. Runner env-release on chunk-unknown (behavioral — the real runner loop)
# --------------------------------------------------------------------------------- #


def _tick_env() -> dict[str, str]:
    fenced = dict(os.environ)
    fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
    return fenced


def _drive(config: RunnerConfig, fenced: dict[str, str], *, ticks: int, pause: float = 0.3) -> None:
    prior = dict(os.environ)
    os.environ.update(fenced)
    try:
        for _ in range(ticks):
            run_single_tick(config)
            time.sleep(pause)
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _pending_outbound(config: RunnerConfig) -> int:
    engine = create_engine_from_url(config.db_url)
    try:
        return len(SqlAlchemyRunnerStore(engine).pending_outbound())
    finally:
        engine.dispose()


def _bindings(config: RunnerConfig, chunk_id: str) -> list:
    engine = create_engine_from_url(config.db_url)
    try:
        return SqlAlchemyRunnerStore(engine).bindings_for_chunk(chunk_id)
    finally:
        engine.dispose()


def test_runner_releases_held_environment_when_hub_reports_chunk_unknown(tmp_path: Path) -> None:
    """The env-release-on-404 path (commit ``68238d0``, blizzard#9): a chunk-scoped
    read reporting 404 is terminal, not transient — the runner reaps whatever it holds
    for the chunk and releases every bound environment. The mock hub's
    ``chunk_unknown`` lever manufactures a genuine 404 on the next
    ``GET /api/fleet/chunks/{id}`` without deleting the chunk's actual seeded state, so
    the same chunk is still readable afterward — proof it was a manufactured read, not
    a deletion.
    """
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        chunk_id = _seed(hub)
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        # Drive until ADVANCE has buffered the build node's completion but PULL has not
        # yet flushed it (the same wedge point test_runner_service.py's unreachable-hub
        # scenario uses) — the runner still holds a live lease and a bound environment
        # for the chunk at this point.
        held = poll_until(
            lambda: _tick_then(config, fenced, lambda: _pending_outbound(config) >= 1),
            timeout=60.0,
        )
        assert held, "the completion never buffered (the mock worker did not run to completion)"
        assert _bindings(config, chunk_id), "no environment was bound to the chunk before the lever fires"

        # Arm the lever: the next chunk-identified GET (the reconcile sweep's ownership
        # check, run before the buffered completion is flushed) reports this chunk
        # unknown exactly once.
        armed = hub.post("/_levers/chunk_unknown", json={"chunk_id": chunk_id, "remaining": 1})
        assert armed.status_code == 200, armed.text

        _drive(config, fenced, ticks=1)

        assert _bindings(config, chunk_id) == [], (
            "the runner did not release the held environment after the hub reported the chunk unknown"
        )

        # The lever is self-expiring (armed with remaining=1, already consumed by the
        # read above) and the chunk's real seeded state was never deleted — it reads
        # normally again, proof the 404 was a manufactured read, not a deletion.
        still_seeded = hub.get(f"/api/fleet/chunks/{chunk_id}")
        assert still_seeded.status_code == 200, still_seeded.text


def _tick_then(config: RunnerConfig, fenced: dict[str, str], check) -> bool:
    _drive(config, fenced, ticks=1)
    return bool(check())
