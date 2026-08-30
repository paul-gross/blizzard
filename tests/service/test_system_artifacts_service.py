"""System-scope artifact reads — service tier (blizzard#391).

A real runner-local API against a real ``blizzard-mock-hub`` subprocess, driven through the
``blizzard runner artifact`` CLI. Proves the end-to-end read, ``bzh:system-scope-reads-live``'s
unreachable-hub failure, and that both packaged names are readable by a worker whose graph
declares no ``artifacts:`` block."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.tokens import TokenHash
from blizzard.hub.system_artifacts import PACKAGED
from blizzard.runner.cli import runner as runner_group
from blizzard.runner.config import RunnerConfig
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from blizzard.runner.store.repository import NewLease
from tests.e2e.test_acceptance_loop import _free_port, _runner_api, _runner_config
from tests.runner_fakes import runner_store_errors
from tests.service.support import mint_fixture, mock_hub, require_mock_fleet, require_winter_source, service_gate

pytestmark = [pytest.mark.service, service_gate]

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _seed_lease(config: RunnerConfig, *, lease_id: str, token: str, graph_id: str) -> None:
    """Mint a lease directly on the runner's own store — no chunk need ever reach the mock
    hub, since a system-scope read never resolves the chunk envelope at all. ``graph_id``
    names a graph nothing ever calls ``record_graph_artifacts`` for, so it is, by
    construction, a graph declaring no ``artifacts:`` block."""
    engine = create_engine_from_url(config.db_url)
    try:
        store = SqlAlchemyRunnerStore(engine, runner_store_errors())
        store.record_lease(
            NewLease(
                lease_id=lease_id,
                chunk_id="ch_service_system_scope",
                graph_id=graph_id,
                node_id="nd_build",
                node_name="build",
                epoch=1,
                runner_id="runner-service",
                retries_max=1,
                created_at=_NOW,
            )
        )
        store.record_lease_token(lease_id, TokenHash(token).hex, _NOW)
    finally:
        engine.dispose()


def _cli_env(config: RunnerConfig, *, lease_id: str, token: str) -> dict[str, str]:
    return {
        "BLIZZARD_LEASE_ID": lease_id,
        "BLIZZARD_RUNNER_URL": f"http://{config.host}:{config.port}",
        "BLIZZARD_LEASE_TOKEN": token,
    }


def test_system_scope_list_and_get_serve_the_seeded_set_end_to_end(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    hub_port = _free_port()

    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post(
            "/_seed/system-artifacts", json={"name": "garden/finding-format", "content": "the finding format text"}
        )
        assert seeded.status_code == 201, seeded.text

        base_config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(base_config, host="127.0.0.1", port=_free_port())
        lease_id, token = "lease_sys_list_get", "tok_sys_list_get"
        _seed_lease(config, lease_id=lease_id, token=token, graph_id="gr_service_system_1")

        with _runner_api(config):
            env = _cli_env(config, lease_id=lease_id, token=token)

            listed = CliRunner().invoke(runner_group, ["artifact", "list", "--scope", "system"], env=env)
            assert listed.exit_code == 0, listed.output
            names = {a["name"] for a in json.loads(listed.output)}
            assert "garden/finding-format" in names

            got_json = CliRunner().invoke(
                runner_group, ["artifact", "get", "garden/finding-format", "--scope", "system"], env=env
            )
            assert got_json.exit_code == 0, got_json.output
            assert json.loads(got_json.output)["scope"] == "system"

            got_content = CliRunner().invoke(
                runner_group,
                ["artifact", "get", "garden/finding-format", "--scope", "system", "--content"],
                env=env,
            )
            assert got_content.exit_code == 0, got_content.output
            assert got_content.output == "the finding format text"


def test_system_scope_read_fails_rather_than_resolving_locally_when_the_hub_is_unreachable(tmp_path: Path) -> None:
    """The system-scope counterpart to ``bzh:graph-scope-reads-local``: unlike a graph-scope
    read, there is no runner-local fallback, so a hub the runner cannot reach must fail the
    read outright rather than ever answering it from nothing."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    unreachable_hub_port = _free_port()  # nothing is listening here

    base_config = _runner_config(tmp_path / "runner", workspace, bin_dir, unreachable_hub_port)
    config = dataclasses.replace(base_config, host="127.0.0.1", port=_free_port())
    lease_id, token = "lease_sys_unreachable", "tok_sys_unreachable"
    _seed_lease(config, lease_id=lease_id, token=token, graph_id="gr_service_system_2")

    with _runner_api(config):
        env = _cli_env(config, lease_id=lease_id, token=token)

        listed = CliRunner().invoke(runner_group, ["artifact", "list", "--scope", "system"], env=env)
        assert listed.exit_code != 0, listed.output

        got = CliRunner().invoke(
            runner_group, ["artifact", "get", "garden/finding-format", "--scope", "system", "--content"], env=env
        )
        assert got.exit_code != 0, got.output
        assert "could not read" in got.output


def test_a_worker_on_a_graph_with_no_artifacts_block_reads_a_packaged_name_verbatim(tmp_path: Path) -> None:
    """This chunk's own acceptance criterion: ``garden/finding-format`` is readable by a
    worker on a graph that declares no ``artifacts:`` at all, and the bytes it gets back are
    exactly the packaged document's own text — not a stand-in fixture string."""
    packaged = PACKAGED.named("garden/finding-format")
    assert packaged is not None, "garden/finding-format must be packaged for this test to prove anything"
    packaged_text = packaged.text

    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    hub_port = _free_port()

    with mock_hub(bin_dir, hub_port) as hub:
        seeded = hub.post("/_seed/system-artifacts", json={"name": "garden/finding-format", "content": packaged_text})
        assert seeded.status_code == 201, seeded.text

        base_config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        config = dataclasses.replace(base_config, host="127.0.0.1", port=_free_port())
        lease_id, token = "lease_sys_no_artifacts_block", "tok_sys_no_artifacts_block"
        # A graph id nothing declares graph-scoped artifacts for — `_graph_rows` reads it
        # empty, exactly as a graph with no `artifacts:` block leaves it.
        _seed_lease(config, lease_id=lease_id, token=token, graph_id="gr_no_artifacts_block")

        with _runner_api(config):
            env = _cli_env(config, lease_id=lease_id, token=token)
            result = CliRunner().invoke(
                runner_group,
                ["artifact", "get", "--scope", "system", "garden/finding-format", "--content"],
                env=env,
            )
            assert result.exit_code == 0, result.output
            assert result.output == packaged_text
