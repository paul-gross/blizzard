"""The one dollar figure, service tier: a real runner whose OpenCode worker turns run under a
subscription-billed ``mock-opencode`` (every step reports ``cost: 0``) prices them off a
``models.json`` in its fenced cache home, and both spend controls compare and render that
estimate as the one ``~``-led figure — the runner ceiling's local-pause reason read off the
runner's own HTTP API, and the chunk cap's spend-cap escalation read off the mock hub."""

from __future__ import annotations

import dataclasses
import json
import os
import time
from pathlib import Path

import httpx
import pytest

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop_wiring import LoopWiring
from tests.fleet_launchers import runner_api, runner_config
from tests.fleet_world import mint_world
from tests.runner_join import fleet_headers
from tests.service.support import (
    mock_hub,
    mock_hub_opencode_chunk_spec,
    poll_until,
    require_mock_fleet,
    service_gate,
)
from tests.support import free_port

pytestmark = [pytest.mark.service, service_gate]

_WORK_REF_URL = "blizzard/toy-api/issues/1"

#: The ``provider/model`` both OpenCode nodes run under — the key the priced cache entry answers.
_PROVIDER, _MODEL = "acme", "coder-1"

#: ``mock-opencode``'s subscription-billed switch (see blizzard-mock's harness README).
_BILLED_ZERO_VAR = "BLIZZARD_MOCK_OPENCODE_BILLED_ZERO"


def _fenced_env(cache_home: Path) -> dict[str, str]:
    fenced = dict(os.environ)
    fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
    fenced[_BILLED_ZERO_VAR] = "1"
    fenced["XDG_CACHE_HOME"] = str(cache_home)
    return fenced


def _priced_cache_home(root: Path) -> Path:
    """A cache home whose ``opencode/models.json`` prices the nodes' model — dear enough that
    any synthesized step's estimate is well above zero."""
    cache = root / "opencode"
    cache.mkdir(parents=True)
    rate = {"input": 1000.0, "output": 1000.0, "cache_read": 1000.0, "cache_write": 1000.0}
    (cache / "models.json").write_text(json.dumps({_PROVIDER: {"models": {_MODEL: {"cost": rate}}}}))
    return root


def _priced_config(tmp_path: Path, workspace: Path, bin_dir: Path, hub_port: int, **spend: float) -> RunnerConfig:
    config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
    return dataclasses.replace(
        config,
        worker_env_passthrough=(*config.worker_env_passthrough, _BILLED_ZERO_VAR, "XDG_CACHE_HOME"),
        **spend,
    )


def _seed(hub: httpx.Client, **extra: object) -> str:
    spec = mock_hub_opencode_chunk_spec(_WORK_REF_URL)
    for node in spec["nodes"].values():
        node["session_model"] = [f"{_PROVIDER}/{_MODEL}"]
    resp = hub.post("/_seed/chunk", json={**spec, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()["chunk_id"]


def _tick(config: RunnerConfig, fenced: dict[str, str]) -> None:
    prior = dict(os.environ)
    os.environ.update(fenced)
    try:
        LoopWiring.of(config).tick_once()
        time.sleep(0.3)
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _detail(hub: httpx.Client, chunk_id: str) -> dict:
    return hub.get(f"/api/fleet/chunks/{chunk_id}", headers=fleet_headers(hub)).json()


def test_runner_ceiling_pauses_on_an_estimated_spend_with_a_tilde_led_reason(tmp_path: Path) -> None:
    """Billed-zero steps carry only an estimate; the ceiling, set under it, still engages the
    local pause, and the reason the runner's HTTP API serves renders the figure ``~$``-led."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_world(bin_dir, tmp_path / "scratch")
    fenced = _fenced_env(_priced_cache_home(tmp_path / "cache"))

    hub_port = free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        _seed(hub)
        config = _priced_config(tmp_path, workspace, bin_dir, hub_port, runner_ceiling_usd=0.0001)

        with runner_api(config), httpx.Client(base_url=config.local_api_url, timeout=10.0) as runner:

            def paused() -> bool:
                _tick(config, fenced)
                return runner.get("/api/runner").json()["pause"]["local"]

            assert poll_until(paused, timeout=90.0), "the spend ceiling never engaged the local pause"
            reason = runner.get("/api/runner").json()["pause"]["local_reason"]

    assert reason is not None and "spend ceiling" in reason, reason
    assert "~$" in reason, f"the estimate-carrying spend did not render as the one ~-led figure: {reason!r}"


def test_chunk_cap_escalates_on_an_estimated_chunk_cost_with_a_tilde_marked_detail(tmp_path: Path) -> None:
    """The hub's derived chunk total carries only an estimate, past the chunk cap: the runner
    escalates the chunk ``spend-cap`` at its next step boundary, the detail ``~``-marked."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_world(bin_dir, tmp_path / "scratch")
    fenced = _fenced_env(_priced_cache_home(tmp_path / "cache"))

    hub_port = free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        chunk_id = _seed(hub, cost={"cost_usd": 0.0, "estimated_cost_usd": 5.0})
        config = _priced_config(tmp_path, workspace, bin_dir, hub_port, chunk_cap_usd=1.0)

        def escalated() -> bool:
            _tick(config, fenced)
            return _detail(hub, chunk_id).get("escalation") is not None

        assert poll_until(escalated, timeout=90.0), f"chunk never escalated (detail {_detail(hub, chunk_id)!r})"
        escalation = _detail(hub, chunk_id)["escalation"]

    assert escalation["cause"] == EscalationCause.SPEND_CAP.value, escalation
    assert escalation["detail"] is not None and "~$5.00" in escalation["detail"], escalation
