"""CLI smoke — the verb surface exists and the real verbs work (unit tier).

The scaffold implements ``init`` / ``migrate`` / ``host`` for real; the remaining
verbs are present as self-naming stubs. This exercises the wiring,
not the daemon runtime (``host`` blocks on a server and is not driven here).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.cli.main import blizzard

pytestmark = pytest.mark.unit


def test_root_lists_hub_and_runner() -> None:
    result = CliRunner().invoke(blizzard, ["--help"])
    assert result.exit_code == 0
    assert "hub" in result.output
    assert "runner" in result.output


def test_hub_lists_its_verbs() -> None:
    result = CliRunner().invoke(blizzard, ["hub", "--help"])
    assert result.exit_code == 0
    for verb in ("init", "migrate", "host", "status", "answer", "ingest", "promote", "requeue"):
        assert verb in result.output


def test_runner_lists_its_verbs() -> None:
    result = CliRunner().invoke(blizzard, ["runner", "--help"])
    assert result.exit_code == 0
    for verb in ("init", "migrate", "host", "heartbeat", "ask", "takeover"):
        assert verb in result.output


def test_hub_init_and_migrate(tmp_path: Path) -> None:
    runner = CliRunner()
    root = str(tmp_path / "hub")

    init_result = runner.invoke(blizzard, ["hub", "init", root])
    assert init_result.exit_code == 0, init_result.output
    assert (tmp_path / "hub" / "blizzard-hub.toml").exists()

    migrate_result = runner.invoke(blizzard, ["hub", "migrate", "--dir", root])
    assert migrate_result.exit_code == 0, migrate_result.output


def test_runner_init(tmp_path: Path) -> None:
    root = str(tmp_path / "runner")
    result = CliRunner().invoke(blizzard, ["runner", "init", root])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "runner" / "blizzard-runner.toml").exists()


# The runtime-dir env fallback (issue #39). Each daemon's dir-taking verbs resolve
# --dir > $BZ_<daemon>_DIR > cwd, so winter's per-env band can aim one feature env at a
# chosen runtime root. Parametrized over both daemons: the two CLIs are parallel, and a
# fallback wired on one but not the other is exactly the drift worth catching.
_DAEMONS = [("hub", "BZ_HUB_DIR", "blizzard-hub.toml"), ("runner", "BZ_RUNNER_DIR", "blizzard-runner.toml")]


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_dir_resolves_from_env_when_flag_absent(daemon: str, env_var: str, config_name: str, tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    cli = CliRunner()
    assert cli.invoke(blizzard, [daemon, "init", str(root)], env={env_var: None}).exit_code == 0

    # No --dir: the env names the runtime root, and `migrate` finds the store there.
    result = cli.invoke(blizzard, [daemon, "migrate"], env={env_var: str(root)})
    assert result.exit_code == 0, result.output
    assert "migrated" in result.output


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_dir_flag_beats_env(daemon: str, env_var: str, config_name: str, tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    cli = CliRunner()
    assert cli.invoke(blizzard, [daemon, "init", str(root)], env={env_var: None}).exit_code == 0

    # The env names a dir that was never initialized, so this only succeeds if --dir wins.
    result = cli.invoke(blizzard, [daemon, "migrate", "--dir", str(root)], env={env_var: str(tmp_path / "unused")})
    assert result.exit_code == 0, result.output
    assert "migrated" in result.output


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_dir_defaults_to_cwd_when_neither_set(
    daemon: str, env_var: str, config_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = CliRunner()
    assert cli.invoke(blizzard, [daemon, "init", str(tmp_path)], env={env_var: None}).exit_code == 0
    monkeypatch.chdir(tmp_path)

    # Neither rung set: unchanged behavior — `.` is the runtime root.
    result = cli.invoke(blizzard, [daemon, "migrate"], env={env_var: None})
    assert result.exit_code == 0, result.output
    assert "migrated" in result.output


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_init_directory_argument_resolves_from_env(daemon: str, env_var: str, config_name: str, tmp_path: Path) -> None:
    # `init`'s positional DIRECTORY honors the same variable, so a band-aimed env
    # scaffolds the root it names rather than the cwd.
    root = tmp_path / "runtime"
    result = CliRunner().invoke(blizzard, [daemon, "init"], env={env_var: str(root)})
    assert result.exit_code == 0, result.output
    assert (root / config_name).exists()


@pytest.mark.parametrize(("daemon", "env_var", "config_name"), _DAEMONS)
def test_dir_help_names_the_env_fallback(daemon: str, env_var: str, config_name: str) -> None:
    result = CliRunner().invoke(blizzard, [daemon, "migrate", "--help"])
    assert result.exit_code == 0
    assert f"${env_var}" in result.output


def test_stub_verb_reports_not_implemented() -> None:
    # `runner status` is still a scaffold stub (ingest and the declarative pause are
    # implemented in this wave); a stub names itself.
    result = CliRunner().invoke(blizzard, ["runner", "status"])
    assert result.exit_code != 0
    assert "not yet implemented" in result.output


def test_hub_host_reports_an_unset_pm_source_token_env_as_a_clean_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `[[pm_source]]` naming an unset `token_env` fails at boot as the same
    clean CLI error the config-load guard raises — not an unhandled traceback.

    The boot failure is by design; the traceback was not. `host` builds the app after
    the revision guard, so this exercises the composition root through the real verb.
    """
    runner = CliRunner()
    root = tmp_path / "hub"
    assert runner.invoke(blizzard, ["hub", "init", str(root)]).exit_code == 0
    monkeypatch.delenv("BZ_PM_TOKEN", raising=False)
    (root / "blizzard-hub.toml").write_text(
        (root / "blizzard-hub.toml").read_text() + '\n[[pm_source]]\nname = "blizzard"\nprovider = "github"\n'
        'repo = "paul-gross/blizzard"\ntoken_env = "BZ_PM_TOKEN"\n'
    )

    result = runner.invoke(blizzard, ["hub", "host", "--dir", str(root)])

    assert result.exit_code != 0
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "BZ_PM_TOKEN" in result.output  # names the variable the operator must set
    # It never claims to be serving a daemon it then fails to build.
    assert "serving blizzard-hub" not in result.output
