"""A traced worker command costs at most 5 ms more at p95 than an untraced one — service tier.

``artifact get`` runs in-process through ``runner_group``, traced and untraced interleaved, against a
real ``blizzard-runner host`` holding a seeded lease. The runner has no ``/v1/traces`` receiver yet,
so the span post is answered with a refusal, which is the cost a traced command pays today."""

from __future__ import annotations

import dataclasses
import math
import time
from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.runner.cli import runner as runner_group
from tests.e2e.test_acceptance_loop import _free_port, _runner_api, _runner_config
from tests.service.support import mint_fixture, mock_hub, require_mock_fleet, require_winter_source, service_gate
from tests.service.test_system_artifacts_service import _cli_env, _seed_lease

pytestmark = [pytest.mark.service, service_gate]

_ROUNDS = 60
_WARMUP = 5
_BUDGET_SECONDS = 0.005
_PARENT = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def test_a_traced_artifact_get_adds_at_most_five_milliseconds_at_p95(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    hub_port = _free_port()

    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/system-artifacts", json={"name": "garden/finding-format", "content": "text"})
        assert seeded.status_code == 201, seeded.text

        config = dataclasses.replace(
            _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port), host="127.0.0.1", port=_free_port()
        )
        lease_id, token = "lease_span_latency", "tok_span_latency"
        _seed_lease(config, lease_id=lease_id, token=token, graph_id="gr_service_span_latency")

        with _runner_api(config):
            untraced_env = _cli_env(config, lease_id=lease_id, token=token)
            traced_env = {**untraced_env, "BLIZZARD_TRACEPARENT": _PARENT}
            argv = ["artifact", "get", "garden/finding-format", "--scope", "system", "--content"]
            untraced: list[float] = []
            traced: list[float] = []

            for round_number in range(_WARMUP + _ROUNDS):
                for env, bucket in ((untraced_env, untraced), (traced_env, traced)):
                    started = time.perf_counter()
                    result = CliRunner().invoke(runner_group, argv, env=env)
                    elapsed = time.perf_counter() - started
                    assert result.exit_code == 0, result.output
                    assert result.output == "text"
                    if round_number >= _WARMUP:
                        bucket.append(elapsed)

    assert _p95(traced) - _p95(untraced) <= _BUDGET_SECONDS, (_p95(traced), _p95(untraced))
