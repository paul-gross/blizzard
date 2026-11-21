"""``blizzard runner prompt`` — the packaged workspace-prompt samples and this runtime's use of them.

Drives the real verbs against a scaffolded runtime root: listing and showing the corpus,
installing a sample as a local fork, diffing that fork for drift, and reporting which source
layer 2 resolves from.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from click.testing import CliRunner, Result

from blizzard.cli.main import blizzard
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.cli.runtime import read_host_stores
from blizzard.runner.harness.workspace_prompts import WORKSPACE_PROMPT_FILENAME
from blizzard.runner.host_records.model import HostRecord
from tests.support.host_files import declare_test_workspace, load_runner_config
from tests.support.runner_fakes import SqlAlchemyRunnerStore, runner_store_errors
from tests.support.runner_init_fakes import init_runner


def _runtime(tmp_path: Path) -> Path:
    root = tmp_path / "runner"
    root.mkdir()
    result = init_runner().invoke(blizzard, ["runner", "init", str(root)])
    assert result.exit_code == 0, result.output
    declare_test_workspace(root)
    return root


def _run(*args: str) -> Result:
    return CliRunner().invoke(blizzard, ["runner", "prompt", *args])


def test_list_and_show_read_the_packaged_corpus() -> None:
    listed = _run("list")
    assert listed.exit_code == 0, listed.output
    assert "winter" in listed.output

    shown = _run("get", "winter")
    assert shown.exit_code == 0, shown.output
    assert "Workspace policy" in shown.output


def test_show_names_the_corpus_when_the_sample_is_unknown() -> None:
    result = _run("get", "no-such-sample")
    assert result.exit_code != 0
    assert "no-such-sample" in result.output
    assert "winter" in result.output


def test_install_forks_the_sample_and_points_the_workspace_record_at_it(tmp_path: Path) -> None:
    """The fork lands as the record's file setting; the host file is not rewritten."""
    root = _runtime(tmp_path)
    before = (root / "blizzard-host.toml").read_bytes()

    result = _run("install", "winter", "--dir", str(root))
    assert result.exit_code == 0, result.output
    assert "stopped host's store" in result.output

    record = _record(root)
    assert record.settings.get("prompt_file") == str(root / WORKSPACE_PROMPT_FILENAME)
    assert "prompt_package" not in record.settings and "prompt" not in record.settings
    assert (root / "blizzard-host.toml").read_bytes() == before
    status = _run("status", "--dir", str(root))
    assert f"from file {root / WORKSPACE_PROMPT_FILENAME}" in status.output


def test_install_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    (root / WORKSPACE_PROMPT_FILENAME).write_text("MINE")
    refused = _run("install", "winter", "--dir", str(root))
    assert refused.exit_code != 0
    assert (root / WORKSPACE_PROMPT_FILENAME).read_text() == "MINE"

    forced = _run("install", "winter", "--force", "--dir", str(root))
    assert forced.exit_code == 0, forced.output
    assert (root / WORKSPACE_PROMPT_FILENAME).read_text() != "MINE"


def test_diff_is_quiet_when_a_fork_matches_and_exits_one_when_it_drifts(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    assert _run("install", "winter", "--dir", str(root)).exit_code == 0

    clean = _run("diff", "winter", "--dir", str(root))
    assert clean.exit_code == 0, clean.output
    assert "no drift" in clean.output

    prompt = root / WORKSPACE_PROMPT_FILENAME
    prompt.write_text(prompt.read_text() + "\nA LOCAL ADDITION\n")
    drifted = _run("diff", "winter", "--dir", str(root))
    assert drifted.exit_code == 1
    assert "A LOCAL ADDITION" in drifted.output


def test_status_names_the_source_for_each_configured_setting(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    unset = _run("status", "--dir", str(root))
    assert unset.exit_code == 0, unset.output
    assert "from none" in unset.output

    _edit(root, 'prompt_package="winter"')
    packaged = _run("status", "--dir", str(root))
    assert packaged.exit_code == 0, packaged.output
    assert 'from package "winter"' in packaged.output


def test_status_fails_when_a_configured_source_resolves_to_nothing(tmp_path: Path) -> None:
    """The shape a rollback to a wheel that ignores the configured setting leaves behind."""
    root = _runtime(tmp_path)
    (root / WORKSPACE_PROMPT_FILENAME).write_text("   \n")
    _edit(root, f'prompt_file="{root / WORKSPACE_PROMPT_FILENAME}"')
    result = _run("status", "--dir", str(root))
    assert result.exit_code != 0
    assert "resolves to nothing" in result.output


def test_a_deprecated_override_row_is_not_what_diff_and_status_report(tmp_path: Path) -> None:
    """Once the record holds the workspace, the deprecated override table is read by nothing but the seed."""
    root = _runtime(tmp_path)
    assert _run("install", "winter", "--dir", str(root)).exit_code == 0
    config = load_runner_config(root)
    store = SqlAlchemyRunnerStore(create_engine_from_url(config.db_url), runner_store_errors())
    store.set_workspace_prompt(config.home_workspace.id, prompt="AN OVERRIDE", at=datetime(2026, 1, 1, tzinfo=UTC))

    status = _run("status", "--dir", str(root))
    assert status.exit_code == 0, status.output
    assert "from file" in status.output
    assert _run("diff", "winter", "--dir", str(root)).exit_code == 0


def test_clear_returns_the_records_prompt_settings_to_their_defaults(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    assert _run("install", "winter", "--dir", str(root)).exit_code == 0

    cleared = _run("clear", "--dir", str(root))

    assert cleared.exit_code == 0, cleared.output
    assert not {"prompt", "prompt_file", "prompt_package"} & _record(root).settings.keys()
    assert "from none" in _run("status", "--dir", str(root)).output


def _record(root: Path) -> HostRecord:
    config = load_runner_config(root)
    with read_host_stores(config) as stores:
        record = stores.workspaces.record(config.home_workspace.id)
    assert record is not None
    return record


def _edit(root: Path, pair: str) -> None:
    workspace_id = load_runner_config(root).home_workspace.id
    result = CliRunner().invoke(blizzard, ["runner", "workspace", "edit", workspace_id, pair, "--dir", str(root)])
    assert result.exit_code == 0, result.output
