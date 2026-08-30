"""``blizzard runner prompt`` — the packaged workspace-prompt samples and this runtime's use of them.

Drives the real verbs against a scaffolded runtime root: listing and showing the corpus,
installing a sample as a local fork, diffing that fork for drift, and reporting which source
layer 2 resolves from (issue #344).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from blizzard.cli.main import blizzard
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.workspace_prompts import WORKSPACE_PROMPT_FILENAME
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from tests.runner_fakes import runner_store_errors

pytestmark = pytest.mark.unit


def _runtime(tmp_path: Path) -> Path:
    root = tmp_path / "runner"
    root.mkdir()
    result = CliRunner().invoke(blizzard, ["runner", "init", str(root)])
    assert result.exit_code == 0, result.output
    return root


def _run(*args: str) -> Result:
    return CliRunner().invoke(blizzard, ["runner", "prompt", *args])


def test_list_and_show_read_the_packaged_corpus() -> None:
    listed = _run("list")
    assert listed.exit_code == 0, listed.output
    assert "winter" in listed.output

    shown = _run("show", "winter")
    assert shown.exit_code == 0, shown.output
    assert "Workspace policy" in shown.output


def test_show_names_the_corpus_when_the_sample_is_unknown() -> None:
    result = _run("show", "no-such-sample")
    assert result.exit_code != 0
    assert "no-such-sample" in result.output
    assert "winter" in result.output


def test_install_forks_the_sample_and_repoints_the_config(tmp_path: Path) -> None:
    """The fork lands as a file knob, and the rest of the config file survives the rewrite."""
    root = _runtime(tmp_path)
    config_text = (root / "blizzard-runner.toml").read_text()
    assert "# Reconciliation-loop seams." in config_text

    result = _run("install", "winter", "--dir", str(root))
    assert result.exit_code == 0, result.output

    reloaded = RunnerConfig.load(root)
    assert reloaded.workspace_prompt_file == str(root / WORKSPACE_PROMPT_FILENAME)
    assert reloaded.workspace_prompt_package == ""
    assert reloaded.resolved_workspace_prompt() == (root / WORKSPACE_PROMPT_FILENAME).read_text()
    # An operator's comments and tables are not collateral of setting one knob.
    assert "# Reconciliation-loop seams." in (root / "blizzard-runner.toml").read_text()


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


def test_status_names_the_source_for_each_configured_knob(tmp_path: Path) -> None:
    root = _runtime(tmp_path)
    unset = _run("status", "--dir", str(root))
    assert unset.exit_code == 0, unset.output
    assert "from none" in unset.output

    _write_knob(root, "workspace_prompt_package", '"winter"')
    packaged = _run("status", "--dir", str(root))
    assert packaged.exit_code == 0, packaged.output
    assert 'from package "winter"' in packaged.output


def test_status_fails_when_a_configured_source_resolves_to_nothing(tmp_path: Path) -> None:
    """The shape a rollback to a wheel that ignores the configured knob leaves behind."""
    root = _runtime(tmp_path)
    (root / WORKSPACE_PROMPT_FILENAME).write_text("   \n")
    _write_knob(root, "workspace_prompt_file", f'"{root / WORKSPACE_PROMPT_FILENAME}"')
    result = _run("status", "--dir", str(root))
    assert result.exit_code != 0
    assert "resolves to nothing" in result.output


def test_install_into_a_config_predating_the_package_knob_keeps_it_parseable(tmp_path: Path) -> None:
    """The branch every pre-#344 config takes: the absent knob is spliced in beside its sibling,
    not between a table's comment block and its header."""
    root = _runtime(tmp_path)
    path = root / "blizzard-runner.toml"
    path.write_text(path.read_text().replace('workspace_prompt_package = ""\n', ""))

    result = _run("install", "winter", "--dir", str(root))
    assert result.exit_code == 0, result.output

    reloaded = RunnerConfig.load(root)  # would raise TOMLDecodeError on a mangled rewrite
    assert reloaded.workspace_prompt_file == str(root / WORKSPACE_PROMPT_FILENAME)
    assert reloaded.workspace_prompt_package == ""
    keys = [line.split("=", 1)[0].strip() for line in path.read_text().splitlines()]
    # The spliced knob joins its siblings as one contiguous run, not adrift above a table header.
    at = [i for i, key in enumerate(keys) if key.startswith("workspace_prompt")]
    assert [keys[i] for i in at] == ["workspace_prompt", "workspace_prompt_file", "workspace_prompt_package"]
    assert at == list(range(at[0], at[0] + 3))


def test_a_standing_override_is_what_diff_and_status_report(tmp_path: Path) -> None:
    """The override is the lane a spawn reads, so the verbs that claim to report the effective
    prompt must not answer from config while one stands."""
    root = _runtime(tmp_path)
    assert _run("install", "winter", "--dir", str(root)).exit_code == 0
    config = RunnerConfig.load(root)
    store = SqlAlchemyRunnerStore(create_engine_from_url(config.db_url), runner_store_errors())
    store.set_workspace_prompt(config.workspace_id, prompt="AN OVERRIDE", at=datetime(2026, 1, 1, tzinfo=UTC))

    status = _run("status", "--dir", str(root))
    assert status.exit_code == 0, status.output
    assert "store override" in status.output

    drifted = _run("diff", "winter", "--dir", str(root))
    assert drifted.exit_code == 1, drifted.output
    assert "AN OVERRIDE" in drifted.output

    reinstalled = _run("install", "winter", "--force", "--dir", str(root))
    assert reinstalled.exit_code == 0, reinstalled.output
    assert "override stands" in reinstalled.output


def _write_knob(root: Path, key: str, value: str) -> None:
    path = root / "blizzard-runner.toml"
    lines = [
        f"{key} = {value}\n" if line.split("=", 1)[0].strip() == key else line
        for line in path.read_text().splitlines(keepends=True)
    ]
    path.write_text("".join(lines))
