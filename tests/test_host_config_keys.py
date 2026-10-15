"""Each key of a host file reaches the ``RunnerConfig`` field a reader takes, defaults when absent, is refused when
malformed, and survives ``to_toml`` and ``load`` unchanged."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from blizzard.runner.config import (
    DEFAULT_CONTEXT_SAMPLE_INTERVAL_SECONDS,
    DEFAULT_RUNNER_CEILING_WINDOW_HOURS,
    DEFAULT_WORKER_STDOUT_RETENTION_DAYS,
    ConfigError,
    RunnerConfig,
)
from blizzard.runner.host_config import HaltedRunner, HostConfig, HostConfigError
from tests.harness_sections import claude_code, opencode
from tests.host_files import write_minimal_host

pytestmark = pytest.mark.unit

_TOKEN = {"BZ_HUB_TOKEN_RUNNER_LOCAL": "tok"}


def _config(root: Path, **kwargs: str) -> RunnerConfig:
    write_minimal_host(root, **kwargs)
    return HostConfig.load(root).runner_config(_TOKEN)


def _refused_runner(root: Path, **kwargs: str) -> str:
    """The runner errors a host file with these keys leaves its one runner halted by."""
    write_minimal_host(root, **kwargs)
    (halted,) = HostConfig.load(root).projections(_TOKEN)
    assert isinstance(halted, HaltedRunner), "the file was accepted"
    return "; ".join(halted.errors)


def _refused_host(root: Path, **kwargs: str) -> str:
    write_minimal_host(root, **kwargs)
    with pytest.raises(HostConfigError) as raised:
        HostConfig.load(root)
    return str(raised.value)


def _round_tripped(root: Path) -> RunnerConfig:
    """The runner the host file projects after being written out again from what it loaded. The written file always
    names the autonomy posture, so where it came from is the one thing a round trip is allowed to change."""
    loaded = HostConfig.load(root)
    (root / "blizzard-host.toml").write_text(loaded.to_toml())
    return replace(HostConfig.load(root).runner_config(_TOKEN), autonomy_source="")


def test_every_knob_defaults_when_its_key_is_absent(tmp_path: Path) -> None:
    config = _config(tmp_path)
    assert config.transcripts_ship is False
    assert (config.transcript_record_max_bytes, config.transcript_chunk_max_bytes) == (None, None)
    assert config.worker_stdout_retention_days == DEFAULT_WORKER_STDOUT_RETENTION_DAYS
    assert config.queue_strict is False
    assert (config.chunk_cap_usd, config.runner_ceiling_usd) == (None, None)
    assert config.runner_ceiling_window_hours == DEFAULT_RUNNER_CEILING_WINDOW_HOURS
    assert (config.context_warn_tokens, config.context_sample_interval_seconds) == (
        None,
        DEFAULT_CONTEXT_SAMPLE_INTERVAL_SECONDS,
    )
    assert config.worker_env_passthrough == () and config.worker_path_prepend == ()
    assert (config.runner_prompt, config.runner_prompt_file, config.transcripts_root) == ("", "", "")
    assert (config.home_workspace.prompt, config.home_workspace.prompt_file, config.home_workspace.prompt_package) == (
        "",
        "",
        "",
    )
    assert config.public_urls == () and config.trusted_proxies == ()
    assert config.subscriptions == () and config.gates == ()
    assert (claude_code(config).enabled, opencode(config).enabled) == (True, True)


def test_every_knob_parses_from_a_hand_written_file_and_survives_a_round_trip(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        top=(
            'public_url = ["https://a.example", "https://b.example"]\n'
            'trusted_proxies = ["127.0.0.1", "10.0.0.0/8"]\n'
            "\n[transcripts]\nship = true\nrecord_max_bytes = 5000\nchunk_max_bytes = 90000\n"
            "\n[worker_stdout]\nretention_days = 3\n"
        ),
        workspace='prompt_file = "wp.md"\n',
        runner=(
            'gates = ["review"]\nrunner_prompt = "be brief"\ntranscripts_root = "/tmp/t"\n'
            "\n[runner.queue]\nstrict = true\n"
            "\n[runner.cost]\nchunk_cap_usd = 5.0\nrunner_ceiling_usd = 50.0\nwindow_hours = 6.0\n"
            "\n[runner.context]\nwarn_tokens = 300000\nsample_interval_seconds = 15\n"
            '\n[runner.worker]\nenv_passthrough = ["FOO"]\npath_prepend = ["/opt/bin"]\n'
        ),
    )
    assert config.public_urls == ("https://a.example", "https://b.example")
    assert config.trusted_proxies == ("127.0.0.1", "10.0.0.0/8")
    assert (config.transcripts_ship, config.transcript_record_max_bytes, config.transcript_chunk_max_bytes) == (
        True,
        5000,
        90000,
    )
    assert config.worker_stdout_retention_days == 3
    assert config.home_workspace.prompt_file == "wp.md"
    assert (config.gates, config.runner_prompt, config.transcripts_root) == (("review",), "be brief", "/tmp/t")
    assert config.queue_strict is True
    assert (config.chunk_cap_usd, config.runner_ceiling_usd, config.runner_ceiling_window_hours) == (5.0, 50.0, 6.0)
    assert (config.context_warn_tokens, config.context_sample_interval_seconds) == (300000, 15)
    assert (config.worker_env_passthrough, config.worker_path_prepend) == (("FOO",), ("/opt/bin",))

    assert _round_tripped(tmp_path) == replace(config, autonomy_source="")


@pytest.mark.parametrize(
    ("kwargs", "complaint"),
    [
        ({"top": "[transcripts]\nship = 1\n"}, "ship"),
        ({"top": "[transcripts]\nrecord_max_bytes = 0\n"}, "positive"),
        ({"top": '[transcripts]\nchunk_max_bytes = "big"\n'}, "integer"),
        ({"top": 'public_url = "not-a-url"\n'}, "public_url"),
        ({"top": 'trusted_proxies = ["not an address"]\n'}, "trusted_proxies"),
        ({"top": "[tracing]\nsettle_seconds = -1\n"}, "tracing"),
    ],
)
def test_a_malformed_host_level_value_refuses_the_file(tmp_path: Path, kwargs: dict[str, str], complaint: str) -> None:
    assert complaint in _refused_host(tmp_path, **kwargs)


@pytest.mark.parametrize(
    ("runner", "complaint"),
    [
        ("[runner.queue]\nstrict = 1\n", "strict"),
        ('[runner.worker]\npath_prepend = ["relative/bin"]\n', "absolute"),
        ('runner_prompt_file = "nowhere.md"\n[runner.claude_code]\nenabled = "yes"\n', "enabled"),
        ('harness_binary = "/a/claude"\n[runner.claude_code]\nbinary = "/b/claude"\n', "not both"),
        ("[runner.claude_code]\nenabled = false\n[runner.opencode]\nenabled = false\n", "enable at least one"),
        ('[runner.harness]\nautonomy = "reckless"\n', "autonomy"),
    ],
)
def test_a_malformed_runner_table_halts_that_runner(tmp_path: Path, runner: str, complaint: str) -> None:
    assert complaint in _refused_runner(tmp_path, runner=runner)


def test_a_worker_path_prepend_expands_a_leading_tilde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = _config(tmp_path / "host", runner='[runner.worker]\npath_prepend = ["~/bin"]\n')
    assert config.worker_path_prepend == (str(tmp_path / "home" / "bin"),)
    assert config.worker_env.path_prepend == config.worker_path_prepend


def test_the_missing_path_prepend_entries_name_only_what_is_absent(tmp_path: Path) -> None:
    present = tmp_path / "present"
    present.mkdir()
    config = _config(
        tmp_path / "host", runner=f'[runner.worker]\npath_prepend = ["{present}", "{tmp_path / "gone"}"]\n'
    )
    assert config.missing_worker_path_prepend_entries == (str(tmp_path / "gone"),)


def _workspace_prompt(config: RunnerConfig) -> str:
    return config.home_workspace.resolved_prompt(config.root)


def test_a_workspace_prompt_resolves_from_its_one_knob(tmp_path: Path) -> None:
    root = tmp_path / "host"
    (root).mkdir()
    (root / "wp.md").write_text("from the file")
    assert _workspace_prompt(_config(root, workspace='prompt = "inline"\n')) == "inline"
    assert _workspace_prompt(_config(root, workspace='prompt_file = "wp.md"\n')) == "from the file"
    with pytest.raises(ConfigError, match="does not exist"):
        _workspace_prompt(_config(root, workspace='prompt_file = "absent.md"\n'))


def test_a_packaged_workspace_prompt_is_exclusive_with_the_text_knobs_and_must_exist(tmp_path: Path) -> None:
    assert _workspace_prompt(_config(tmp_path / "a", workspace='prompt_package = "winter"\n'))
    with pytest.raises(ConfigError, match="exclusive"):
        _workspace_prompt(_config(tmp_path / "b", workspace='prompt_package = "winter"\nprompt = "x"\n'))
    with pytest.raises(ConfigError, match="no packaged sample"):
        _workspace_prompt(_config(tmp_path / "c", workspace='prompt_package = "nope"\n'))


def test_a_runner_prompt_file_wins_over_the_inline_text_and_a_missing_one_raises(tmp_path: Path) -> None:
    root = tmp_path / "host"
    root.mkdir()
    (root / "rp.md").write_text("from the file")
    config = _config(root, runner='runner_prompt = "inline"\nrunner_prompt_file = "rp.md"\n')
    assert config.resolved_runner_prompt() == "from the file"
    with pytest.raises(ConfigError, match="does not exist"):
        _config(root, runner='runner_prompt_file = "absent.md"\n').resolved_runner_prompt()


def test_the_declared_subscriptions_are_what_a_runner_names_and_nothing_is_synthesized(tmp_path: Path) -> None:
    top = (
        '[[subscription]]\nslug = "anthropic"\nname = "Anthropic"\nprovider = "anthropic"\n'
        'credentials_path = "/c.json"\nsample_interval_seconds = 120\n'
        '\n[[subscription]]\nslug = "openai"\nname = "OpenAI"\nprovider = "openai"\n'
    )
    naming = _config(tmp_path / "a", top=top, runner='subscription = "anthropic"\n')
    (chosen,) = naming.resolved_subscriptions()
    assert (chosen.slug, chosen.credentials_path, chosen.sample_interval_seconds) == ("anthropic", "/c.json", 120)
    assert _round_tripped(tmp_path / "a") == replace(naming, autonomy_source="")

    declaring_but_naming_none = _config(tmp_path / "b", top=top)
    assert declaring_but_naming_none.resolved_subscriptions() == ()
    assert _config(tmp_path / "c").resolved_subscriptions() == ()


@pytest.mark.parametrize(
    ("top", "complaint"),
    [
        ('[[subscription]]\nslug = "a"\nname = "A"\n', "provider"),
        (
            '[[subscription]]\nslug = "a"\nname = "A"\nprovider = "p"\n[[subscription]]\nslug = "a"\nname = "B"\nprovider = "p"\n',
            "duplicate",
        ),
        ('[[subscription]]\nslug = ""\nname = "A"\nprovider = "p"\n', "slug must not be empty"),
        ('subscription = { slug = "a" }\n', "array of tables"),
    ],
)
def test_a_malformed_subscription_declaration_refuses_the_file(tmp_path: Path, top: str, complaint: str) -> None:
    assert complaint in _refused_host(tmp_path, top=top)


def test_the_session_secret_variable_round_trips_by_name_and_resolves_the_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import base64

    secret = base64.b64encode(b"s" * 48).decode()
    monkeypatch.setenv("MY_SESSION", secret)
    config = _config(tmp_path, top='session_secret_env = "MY_SESSION"\n')
    assert (config.session_secret_env, config.session_secret) == ("MY_SESSION", b"s" * 48)
    assert secret not in HostConfig.load(tmp_path).to_toml()


def test_the_default_store_is_omitted_from_the_written_file_and_a_custom_one_is_kept(tmp_path: Path) -> None:
    default = _config(tmp_path / "a")
    assert "db_url" not in HostConfig.load(tmp_path / "a").to_toml()
    assert default.db_url.endswith("/data/host.db")
    custom: Any = _config(tmp_path / "b", top='db_url = "postgresql://runner@db.test/runner"\n')
    assert custom.db_url == "postgresql://runner@db.test/runner"
    assert 'db_url = "postgresql://runner@db.test/runner"' in HostConfig.load(tmp_path / "b").to_toml()
