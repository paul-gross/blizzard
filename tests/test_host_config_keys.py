"""Each key of a host file reaches the ``RunnerConfig`` field a reader takes, defaults when absent, is refused when
malformed, and survives ``to_toml`` and ``load`` unchanged."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from blizzard.foundation.config_keys import (
    ARRAY_OF_TABLES,
    BOOLEAN,
    INTEGER,
    NUMBER,
    OPEN_TABLE,
    STRING_LIST,
    STRING_OR_LIST,
    TABLE,
    ConfigRegistry,
    ConfigView,
    UndeclaredConfigKey,
)
from blizzard.runner import host_config
from blizzard.runner.config import (
    DEFAULT_CONTEXT_SAMPLE_INTERVAL_SECONDS,
    DEFAULT_RUNNER_CEILING_WINDOW_HOURS,
    DEFAULT_WORKER_STDOUT_RETENTION_DAYS,
    ConfigError,
    RunnerConfig,
    worker_shell_refusal,
)
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.host_config import (
    _GRADUATED_WORKSPACE_KEYS,
    _RETIRED_KEYS,
    HaltedRunner,
    HostConfigError,
    _graduated_raw,
    _parse,
)
from blizzard.runner.host_layout import HostLayout
from tests.config_inputs import FLAG_REGISTRY, load_host_config
from tests.harness_sections import claude_code, opencode
from tests.host_files import write_minimal_host
from tests.runner_fakes import NO_TRACING

pytestmark = pytest.mark.unit

_TOKEN = {"BZ_HUB_TOKEN_RUNNER_LOCAL": "tok"}


def _config(root: Path, **kwargs: str) -> RunnerConfig:
    write_minimal_host(root, **kwargs)
    return load_host_config(root).runner_config(_TOKEN)


def _refused_runner(root: Path, **kwargs: str) -> str:
    """The runner errors a host file with these keys leaves its one runner halted by."""
    write_minimal_host(root, **kwargs)
    (halted,) = load_host_config(root).projections(_TOKEN)
    assert isinstance(halted, HaltedRunner), "the file was accepted"
    return "; ".join(halted.errors)


def _refused_host(root: Path, **kwargs: str) -> str:
    write_minimal_host(root, **kwargs)
    with pytest.raises(HostConfigError) as raised:
        load_host_config(root)
    return str(raised.value)


def _round_tripped(root: Path) -> RunnerConfig:
    """The runner the host file projects after being written out again from what it loaded. The written file always
    names the autonomy posture, so where it came from is the one thing a round trip is allowed to change."""
    loaded = load_host_config(root)
    (root / "blizzard-host.toml").write_text(loaded.to_toml())
    return replace(load_host_config(root).runner_config(_TOKEN), autonomy_source="")


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
    assert config.worker_shell == "/bin/bash"
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
            '\n[runner.worker]\nenv_passthrough = ["FOO"]\npath_prepend = ["/opt/bin"]\nshell = "/opt/bin/sh"\n'
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
    assert config.worker_shell == "/opt/bin/sh"
    assert config.worker_env(NO_TRACING).worker({}).variables["CLAUDE_CODE_SHELL"] == "/opt/bin/sh"

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
        ('[runner.worker]\nshell = "bash"\n', "shell must be absolute"),
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
    assert config.worker_env(NO_TRACING).path_prepend == config.worker_path_prepend


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
    assert secret not in load_host_config(tmp_path).to_toml()


def test_the_default_store_is_omitted_from_the_written_file_and_a_custom_one_is_kept(tmp_path: Path) -> None:
    default = _config(tmp_path / "a")
    assert "db_url" not in load_host_config(tmp_path / "a").to_toml()
    assert default.db_url.endswith("/data/host.db")
    custom: Any = _config(tmp_path / "b", top='db_url = "postgresql://runner@db.test/runner"\n')
    assert custom.db_url == "postgresql://runner@db.test/runner"
    assert 'db_url = "postgresql://runner@db.test/runner"' in load_host_config(tmp_path / "b").to_toml()


def test_every_retired_key_graduates_to_its_placement_and_never_into_the_runner() -> None:
    legacy: dict[str, object] = {key: f"v-{key}" for key in _RETIRED_KEYS}
    legacy.update(workspace_envs=["e1"], workspace_repo=[], max_agents=2, external_subscription_usage={})

    raw = _graduated_raw(legacy, runner_id="rn_graduated", served=("p",))

    (runner,) = raw["runner"]
    (workspace,) = raw["workspace"]
    assert not set(_RETIRED_KEYS) & (set(runner) | set(raw))
    assert {_GRADUATED_WORKSPACE_KEYS[key] for key in _RETIRED_KEYS if key in _GRADUATED_WORKSPACE_KEYS} <= set(
        workspace
    )
    assert runner["agents"] == 2
    assert all("agents" not in binding for binding in runner["projects"])
    assert runner["subscription"] == "anthropic"


#: Values a key of each type takes that the loader accepts; a key the loader checks harder has its own below.
_SAMPLE_BY_TYPE: dict[str, object] = {
    BOOLEAN: True,
    INTEGER: 1,
    NUMBER: 0.5,
    STRING_LIST: ["x"],
    STRING_OR_LIST: "https://a.example",
}
_SAMPLE_BY_PATH: dict[str, object] = {
    "loop": "stepped",
    "trusted_proxies": ["127.0.0.1"],
    "workspace[].provider": "basic",
    "workspace[].prompt_package": "",
    "workspace[].repo[].name": "repo",
    "runner[].subscription": "sub",
    "subscription[].slug": "sub",
    "subscription[].usage_api_base": "http://usage.test",
    "runner[].projects[].workspace": "ws",
    "workspace[].id": "ws",
    "runner[].harness.autonomy": "dangerous",
    "runner[].harness.config_dir": "/opt/harness",
    "runner[].opencode.models.supported": ["openai/*"],
    "runner[].worker.path_prepend": ["/opt/bin"],
    "runner[].worker.shell": "/opt/bin/sh",
    "runner[].throttle.budget.usd": 0.25,
    "runner[].throttle.schedule.days": ["mon"],
    "runner[].throttle.schedule.hours": ["19:00-08:00"],
    "runner[].throttle.on_decline": "release",
    "runner[].throttle.on_usage_limit": "release",
    "tracing.platform_sample_ratio": 0.5,
    "tracing.worker_programs": False,
}


def _document_carrying_every_key(
    registry: ConfigRegistry, overrides: dict[str, object] | None = None
) -> dict[str, Any]:
    """A host document holding every declared key that is not deprecated, nested as the file nests it."""
    document: dict[str, Any] = {}
    for key in registry.keys:
        if key.deprecated:
            continue
        *parents, leaf = key.path.split(".")
        table = document
        for parent in parents:
            name = parent.removesuffix("[]")
            if parent.endswith("[]"):
                entries = table.setdefault(name, [{}])
                table = entries[0]
            else:
                table = table.setdefault(name, {})
        if key.type in (TABLE, OPEN_TABLE):
            table.setdefault(leaf, {})
        elif key.type == ARRAY_OF_TABLES:
            table.setdefault(leaf.removesuffix("[]"), [{}])
        else:
            table[leaf] = {**_SAMPLE_BY_PATH, **(overrides or {})}.get(key.path, _SAMPLE_BY_TYPE.get(key.type, "x"))
    document["flags"] = {"probe": True}
    return document


def test_every_declared_host_key_is_read_by_a_load_that_carries_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    read: set[str] = set()
    checked = ConfigView._check  # pyright: ignore[reportPrivateUsage]

    def recording(view: ConfigView, key: str) -> None:
        read.add(view._join(key))  # pyright: ignore[reportPrivateUsage]
        checked(view, key)

    monkeypatch.setattr(ConfigView, "_check", recording)
    loaded = _parse(
        _document_carrying_every_key(HOST_REGISTRY),
        layout=HostLayout(tmp_path),
        host=None,
        port=None,
        loop=None,
        flag_registry=FLAG_REGISTRY,
        strict=True,
    )
    loaded.host_view()  # the lazily read tables — [auth] — are read when projected

    unread = [key.path for key in HOST_REGISTRY.keys if key.type != OPEN_TABLE and key.path not in read]
    assert unread == []


def test_an_undeclared_host_key_read_names_the_key_and_the_host_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    without_port = ConfigRegistry("host", tuple(key for key in HOST_REGISTRY.keys if key.path != "port"))
    monkeypatch.setattr(host_config, "HOST_REGISTRY", without_port)
    write_minimal_host(tmp_path)
    with pytest.raises(UndeclaredConfigKey, match=r"'port'.*host") as raised:
        load_host_config(tmp_path)
    assert (raised.value.path, raised.value.registry) == ("port", "host")


def test_a_host_file_carrying_an_undeclared_key_loads_when_read_leniently(tmp_path: Path) -> None:
    write_minimal_host(tmp_path, top="undeclared_probe = 1\n[flags]\nprobe = true\n")
    assert load_host_config(tmp_path, strict=False).runner_config(_TOKEN).name == "runner-local"


def _subscription_top(base: str) -> str:
    return (
        f'[[subscription]]\nslug = "anthropic"\nname = "Anthropic"\nprovider = "anthropic"\nusage_api_base = {base}\n'
    )


def test_a_declared_usage_api_base_round_trips_without_its_trailing_slash(tmp_path: Path) -> None:
    top = _subscription_top('"https://gateway.test/anthropic/"')
    config = _config(tmp_path, top=top, runner='subscription = "anthropic"\n')

    (chosen,) = config.resolved_subscriptions()
    assert chosen.usage_api_base == "https://gateway.test/anthropic"
    assert _round_tripped(tmp_path) == replace(config, autonomy_source="")


def test_an_undeclared_usage_api_base_is_none(tmp_path: Path) -> None:
    top = '[[subscription]]\nslug = "anthropic"\nname = "Anthropic"\nprovider = "anthropic"\n'
    (chosen,) = _config(tmp_path, top=top, runner='subscription = "anthropic"\n').resolved_subscriptions()

    assert chosen.usage_api_base is None


@pytest.mark.parametrize(
    "base",
    [
        '"gateway.test"',
        '"ftp://gateway.test"',
        '"http://"',
        '"https://h/p?x=1"',
        '"https://h/p#f"',
        '"https://u:pw@h/p"',
        "7",
    ],
)
def test_a_malformed_usage_api_base_is_refused_without_echoing_the_value(tmp_path: Path, base: str) -> None:
    write_minimal_host(tmp_path, top=_subscription_top(base), runner='subscription = "anthropic"\n')
    with pytest.raises(HostConfigError) as raised:
        load_host_config(tmp_path)

    errors = str(raised.value)
    assert "usage_api_base must be an http(s) URL" in errors
    assert base.strip('"') not in errors.replace("http(s)", "")


def test_a_worker_shell_that_is_missing_or_not_executable_is_refused(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.write_text("#!/bin/sh\n")
    plain.chmod(0o644)
    runnable = tmp_path / "runnable"
    runnable.write_text("#!/bin/sh\n")
    runnable.chmod(0o755)
    assert "does not exist" in (worker_shell_refusal(str(tmp_path / "gone")) or "")
    assert "not executable" in (worker_shell_refusal(str(plain)) or "")
    assert worker_shell_refusal(str(runnable)) is None
    assert worker_shell_refusal("/bin/bash") is None
