"""``[harness] autonomy`` — the one runner-wide value each harness binding translates.

Argv per value, invocation kind, and harness, the config resolution rules, and the guarantee that
runner-owned denials hold under every value."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

import pytest

from blizzard.runner.config import (
    CONFIG_FILENAME,
    OPENCODE_WORKER_CONFIG_FILENAME,
    WORKER_SETTINGS_FILENAME,
    ConfigError,
    RunnerConfig,
)
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import HarnessSpawnError, WorkerPreamble
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.internal.claude_code_adapter import ClaudeCodeAdapter
from blizzard.runner.harness.internal.claude_code_bundle import ClaudeCodeBundleDelivery
from blizzard.runner.harness.internal.claude_code_denials import CLAUDE_CODE_DENIED_TOOLS
from blizzard.runner.harness.internal.opencode_adapter import OpenCodeAdapter
from blizzard.runner.harness.internal.opencode_permission_resolver import (
    OpenCodeEffectivePermissions,
    OpenCodePermissionResolveError,
)
from blizzard.runner.harness.internal.opencode_shapes import OpenCodePermissionRule
from blizzard.runner.harness.process_launch import LaunchedProcess
from blizzard.runner.runtime import Runtime
from tests.runner_fakes import FakeProbe, make_envelope

pytestmark = pytest.mark.unit

_ALL = list(Autonomy)


class _RecordingLauncher:
    """Records every argv it is asked to launch; starts nothing."""

    def __init__(self) -> None:
        self.argvs: list[list[str]] = []
        self.envs: list[dict[str, str]] = []

    def launch(
        self,
        argv: list[str],
        *,
        cwd: str | None,
        env: dict[str, str],
        stdout: IO[bytes] | int | None,
        stderr: IO[bytes] | int | None,
        defer_disarm: bool = False,
    ) -> LaunchedProcess:
        self.argvs.append(list(argv))
        self.envs.append(dict(env))
        return LaunchedProcess(pid=4242, pgid=4242, process_start_time="1", confirm_durable=lambda: None)


def _preamble(workdir: Path) -> WorkerPreamble:
    return WorkerPreamble(
        environments=[AcquiredEnvironment(environment_id="e1", workdir=str(workdir))],
        lease_id="lease_1",
        local_api_url="http://127.0.0.1:8431",
        stdout_path=str(workdir / "out.log"),
        stderr_path=str(workdir / "err.log"),
    )


def _claude(launcher: _RecordingLauncher, **kwargs: Any) -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter(
        "claude", worker_env=AllowlistedEnv.of(()), process=FakeProbe(), launcher=launcher, **kwargs
    )


def _rule(permission: str, pattern: str, action: str) -> OpenCodePermissionRule:
    return OpenCodePermissionRule(permission, pattern, action)


class _FakeResolver:
    """Resolves from a canned answer; once the launch env carries a ``bash`` ask override it reports the
    asks gone (``stuck=False``) or still present (``stuck=True``), as OpenCode's own re-resolution would."""

    def __init__(self, *, ask: bool = True, stuck: bool = False, error: Exception | None = None) -> None:
        self.calls: list[dict[str, str]] = []
        self._ask = ask
        self._stuck = stuck
        self._error = error

    def resolve(self, *, cwd: str, env: Any) -> OpenCodeEffectivePermissions:
        self.calls.append(dict(env))
        if self._error is not None:
            raise self._error
        composed = '"bash": "deny"' in env.get("OPENCODE_CONFIG_CONTENT", "")
        asking = self._ask and (self._stuck or not composed)
        rules = (_rule("*", "*", "allow"), _rule("bash", "*", "ask" if asking else "deny"))
        return OpenCodeEffectivePermissions(
            merged_config={"permission": {"bash": "ask"}}, agent_rulesets={"build": rules}
        )


def _opencode(launcher: _RecordingLauncher, **kwargs: Any) -> OpenCodeAdapter:
    kwargs.setdefault("permission_resolver", _FakeResolver(ask=False))
    return OpenCodeAdapter(
        "opencode", worker_env=AllowlistedEnv.of(()), process=FakeProbe(), launcher=launcher, **kwargs
    )


def _unattended(adapter: Any, workdir: Path, launcher: _RecordingLauncher) -> dict[str, list[str]]:
    """The argv each unattended kind launched, keyed by kind."""
    preamble = _preamble(workdir)
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    kinds: dict[str, Callable[[], object]] = {
        "spawn": lambda: adapter.spawn(envelope, preamble, session_hint="sess-1"),
        "resume": lambda: adapter.spawn(envelope, preamble, session_hint=None, resume_from="sess-1"),
        "nudge": lambda: adapter.resume_with_message(str(workdir), "sess-1", "go on", preamble=preamble),
        "judge": lambda: adapter.judge(str(workdir), "sess-1", "judge it", str(workdir / "j.out"), preamble=preamble),
    }
    seen: dict[str, list[str]] = {}
    for kind, run in kinds.items():
        before = len(launcher.argvs)
        run()
        assert len(launcher.argvs) == before + 1, kind
        seen[kind] = launcher.argvs[-1]
    return seen


def _flag(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else None


_CLAUDE_MODES = {Autonomy.Normal: "manual", Autonomy.Auto: "auto", Autonomy.Dangerous: "bypassPermissions"}


@pytest.mark.parametrize("autonomy", _ALL)
def test_claude_code_maps_autonomy_on_every_unattended_kind(tmp_path: Path, autonomy: Autonomy) -> None:
    launcher = _RecordingLauncher()
    adapter = _claude(launcher, autonomy=autonomy)

    for kind, argv in _unattended(adapter, tmp_path, launcher).items():
        assert _flag(argv, "--permission-mode") == _CLAUDE_MODES[autonomy], kind
        expected = "none" if autonomy is Autonomy.Normal else None
        assert _flag(argv, "--permission-prompts") == expected, kind


@pytest.mark.parametrize("autonomy", _ALL)
def test_claude_code_takeover_reasserts_the_mode_and_never_denies_prompts(autonomy: Autonomy) -> None:
    adapter = _claude(_RecordingLauncher(), autonomy=autonomy)

    attended = adapter.resume_command("/w", "s-1", attended=True)

    assert attended == f"cd /w && claude --resume s-1 --permission-mode {_CLAUDE_MODES[autonomy]}"
    assert adapter.resume_command("/w", "s-1") == "cd /w && claude --resume s-1"


@pytest.mark.parametrize("autonomy", _ALL)
def test_a_legacy_override_replaces_the_mapped_flags(tmp_path: Path, autonomy: Autonomy) -> None:
    launcher = _RecordingLauncher()
    adapter = _claude(launcher, autonomy=autonomy, permission_mode="acceptEdits")

    for kind, argv in _unattended(adapter, tmp_path, launcher).items():
        assert _flag(argv, "--permission-mode") == "acceptEdits", kind
        assert "--permission-prompts" not in argv, kind
    assert adapter.resume_command("/w", "s-1", attended=True).endswith(" --permission-mode acceptEdits")


def test_an_empty_legacy_override_omits_the_permission_flags(tmp_path: Path) -> None:
    launcher = _RecordingLauncher()
    adapter = _claude(launcher, permission_mode="")

    for kind, argv in _unattended(adapter, tmp_path, launcher).items():
        assert "--permission-mode" not in argv, kind
        assert "--permission-prompts" not in argv, kind
    assert adapter.resume_command("/w", "s-1", attended=True) == "cd /w && claude --resume s-1"


@pytest.mark.parametrize("autonomy", _ALL)
def test_opencode_passes_auto_on_every_unattended_kind_except_normal(tmp_path: Path, autonomy: Autonomy) -> None:
    launcher = _RecordingLauncher()
    adapter = _opencode(launcher, autonomy=autonomy)

    for kind, argv in _unattended(adapter, tmp_path, launcher).items():
        assert ("--auto" in argv) is (autonomy is not Autonomy.Normal), kind


def _content(env: dict[str, str]) -> dict[str, Any]:
    return json.loads(env["OPENCODE_CONFIG_CONTENT"])


def test_opencode_normal_composes_ask_denials_into_every_unattended_kind(tmp_path: Path) -> None:
    launcher = _RecordingLauncher()
    resolver = _FakeResolver()
    adapter = _opencode(launcher, autonomy=Autonomy.Normal, permission_resolver=resolver)

    kinds = _unattended(adapter, tmp_path, launcher)

    assert len(launcher.envs) == len(kinds)
    for env in launcher.envs:
        assert _content(env)["permission"] == {"bash": "deny"}
    assert len(resolver.calls) == 2 * len(kinds)


def test_opencode_normal_keeps_the_published_document_and_adds_only_the_overrides(tmp_path: Path) -> None:
    config = tmp_path / "opencode.json"
    config.write_text(json.dumps({"permission": {"question": "deny", "bash": "ask"}, "plugin": ["p"]}))
    launcher = _RecordingLauncher()
    adapter = _opencode(
        launcher, autonomy=Autonomy.Normal, permission_resolver=_FakeResolver(), worker_config_path=str(config)
    )

    _unattended(adapter, tmp_path, launcher)

    for env in launcher.envs:
        assert env["OPENCODE_CONFIG"] == str(config)
        assert _content(env) == {"permission": {"question": "deny", "bash": "deny"}, "plugin": ["p"]}
    assert json.loads(config.read_text())["permission"]["bash"] == "ask"


@pytest.mark.parametrize("autonomy", [Autonomy.Auto, Autonomy.Dangerous])
def test_opencode_auto_and_dangerous_carry_no_overrides_and_never_resolve(tmp_path: Path, autonomy: Autonomy) -> None:
    launcher = _RecordingLauncher()
    resolver = _FakeResolver()
    adapter = _opencode(launcher, autonomy=autonomy, permission_resolver=resolver)

    _unattended(adapter, tmp_path, launcher)

    assert resolver.calls == []
    assert all("OPENCODE_CONFIG_CONTENT" not in env for env in launcher.envs)


def test_opencode_normal_with_nothing_to_deny_launches_unchanged(tmp_path: Path) -> None:
    launcher = _RecordingLauncher()
    adapter = _opencode(launcher, autonomy=Autonomy.Normal, permission_resolver=_FakeResolver(ask=False))

    _unattended(adapter, tmp_path, launcher)

    assert all("OPENCODE_CONFIG_CONTENT" not in env for env in launcher.envs)


def test_opencode_takeover_env_carries_no_overrides(tmp_path: Path) -> None:
    launcher = _RecordingLauncher()
    resolver = _FakeResolver()
    adapter = _opencode(launcher, autonomy=Autonomy.Normal, permission_resolver=resolver)

    env = adapter.identity_env(_preamble(tmp_path), "ch_1", "sess-1")

    assert "OPENCODE_CONFIG_CONTENT" not in env
    assert resolver.calls == []


@pytest.mark.parametrize(
    ("resolver", "needle"),
    [
        (_FakeResolver(stuck=True), "permission 'bash' pattern '*'"),
        (_FakeResolver(error=OpenCodePermissionResolveError("boom")), "boom"),
        (None, "no OpenCode permission resolver"),
    ],
)
def test_opencode_normal_refuses_to_launch_what_it_cannot_prove(tmp_path: Path, resolver: Any, needle: str) -> None:
    launcher = _RecordingLauncher()
    adapter = OpenCodeAdapter(
        "opencode",
        worker_env=AllowlistedEnv.of(()),
        process=FakeProbe(),
        launcher=launcher,
        autonomy=Autonomy.Normal,
        permission_resolver=resolver,
    )

    with pytest.raises(HarnessSpawnError) as caught:
        _unattended(adapter, tmp_path, launcher)

    assert needle in str(caught.value)
    assert launcher.argvs == []


@pytest.mark.parametrize("autonomy", _ALL)
def test_opencode_takeover_never_carries_auto(autonomy: Autonomy) -> None:
    adapter = _opencode(_RecordingLauncher(), autonomy=autonomy)

    assert "--auto" not in adapter.resume_command("/w", "s-1", attended=True)


# --------------------------------------------------------------------------- #
# Config resolution.


def _load(root: Path, body: str) -> RunnerConfig:
    (root / CONFIG_FILENAME).write_text(f'db_url = "{RunnerConfig.default_db_url(root)}"\n{body}')
    return RunnerConfig.load(root)


@pytest.mark.parametrize("autonomy", _ALL)
def test_each_autonomy_value_parses(tmp_path: Path, autonomy: Autonomy) -> None:
    config = _load(tmp_path, f'[harness]\nautonomy = "{autonomy.value}"\n')

    assert config.autonomy is autonomy
    assert config.harness_permission_mode is None


def test_neither_key_resolves_to_dangerous(tmp_path: Path) -> None:
    config = _load(tmp_path, "")

    assert config.autonomy is Autonomy.Dangerous
    assert config.harness_permission_mode is None


def test_legacy_only_keeps_the_override_and_resolves_dangerous(tmp_path: Path) -> None:
    config = _load(tmp_path, 'harness_permission_mode = "acceptEdits"\n')

    assert config.autonomy is Autonomy.Dangerous
    assert config.harness_permission_mode == "acceptEdits"


def test_legacy_empty_stays_distinguishable_from_absent(tmp_path: Path) -> None:
    assert _load(tmp_path, 'harness_permission_mode = ""\n').harness_permission_mode == ""


@pytest.mark.parametrize("legacy", ["bypassPermissions", ""])
def test_both_keys_fail_naming_both_and_the_path(tmp_path: Path, legacy: str) -> None:
    body = f'harness_permission_mode = "{legacy}"\n[harness]\nautonomy = "auto"\n'

    with pytest.raises(ConfigError) as caught:
        _load(tmp_path, body)

    message = str(caught.value)
    assert "harness_permission_mode" in message
    assert "[harness] autonomy" in message
    assert str(tmp_path / CONFIG_FILENAME) in message


def test_an_unknown_autonomy_fails_with_the_value_the_allowed_set_and_the_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as caught:
        _load(tmp_path, '[harness]\nautonomy = "bogus"\n')

    message = str(caught.value)
    assert "'bogus'" in message
    assert all(f"'{value.value}'" in message for value in Autonomy)
    assert str(tmp_path / CONFIG_FILENAME) in message


def test_the_scaffold_writes_dangerous_and_no_legacy_key(tmp_path: Path) -> None:
    config = Runtime(tmp_path).init()

    text = config.config_path.read_text()
    assert '[harness]\nautonomy = "dangerous"\n' in text
    assert "harness_permission_mode" not in text
    assert RunnerConfig.load(tmp_path).autonomy is Autonomy.Dangerous


# --------------------------------------------------------------------------- #
# Runner-owned denials hold under every value.


@pytest.mark.parametrize("autonomy", _ALL)
def test_runner_owned_denials_hold_under_every_autonomy(tmp_path: Path, autonomy: Autonomy) -> None:
    Runtime(tmp_path).init()
    path = tmp_path / CONFIG_FILENAME
    path.write_text(path.read_text().replace('autonomy = "dangerous"', f'autonomy = "{autonomy.value}"'))

    config = Runtime(tmp_path).init()

    assert config.autonomy is autonomy
    settings = json.loads((tmp_path / WORKER_SETTINGS_FILENAME).read_text())
    assert settings["permissions"]["deny"] == list(CLAUDE_CODE_DENIED_TOOLS)
    opencode = json.loads((tmp_path / OPENCODE_WORKER_CONFIG_FILENAME).read_text())
    assert opencode["permission"]["question"] == "deny"


@pytest.mark.parametrize("autonomy", _ALL)
def test_claude_code_still_delivers_the_settings_file_under_every_autonomy(tmp_path: Path, autonomy: Autonomy) -> None:
    launcher = _RecordingLauncher()
    adapter = _claude(launcher, autonomy=autonomy, settings_path="/runner/worker-settings.json")

    for kind, argv in _unattended(adapter, tmp_path, launcher).items():
        assert _flag(argv, "--settings") == "/runner/worker-settings.json", kind


# --------------------------------------------------------------------------- #
# A published bundle: the composed settings and companion flags ride every unattended kind.


def _delivery(tmp_path: Path, *, companions: bool) -> ClaudeCodeBundleDelivery:
    root = tmp_path / "snap"
    return ClaudeCodeBundleDelivery(
        root / "settings.json",
        root / "mcp.json" if companions else None,
        root / "agents.json" if companions else None,
        root / "plugins" if companions else None,
    )


@pytest.mark.parametrize("autonomy", _ALL)
def test_a_bundle_delivers_settings_and_companions_on_every_unattended_kind(tmp_path: Path, autonomy: Autonomy) -> None:
    launcher = _RecordingLauncher()
    delivery = _delivery(tmp_path, companions=True)
    adapter = _claude(launcher, autonomy=autonomy, settings_path="/runner/worker-settings.json", bundle=delivery)

    for kind, argv in _unattended(adapter, tmp_path, launcher).items():
        assert _flag(argv, "--settings") == str(delivery.settings), kind
        assert f"--mcp-config={delivery.mcp_config}" in argv, kind
        assert _flag(argv, "--agents") == str(delivery.agents), kind
        assert _flag(argv, "--plugin-dir") == str(delivery.plugins), kind
        assert "/runner/worker-settings.json" not in argv, kind


def test_a_bundle_without_companions_passes_only_the_settings(tmp_path: Path) -> None:
    launcher = _RecordingLauncher()
    adapter = _claude(launcher, bundle=_delivery(tmp_path, companions=False))

    for kind, argv in _unattended(adapter, tmp_path, launcher).items():
        assert _flag(argv, "--settings") is not None, kind
        assert not [a for a in argv if a.startswith(("--mcp-config", "--agents", "--plugin-dir"))], kind


def test_the_prompt_stays_the_final_argument_with_a_bundle(tmp_path: Path) -> None:
    launcher = _RecordingLauncher()
    adapter = _claude(launcher, bundle=_delivery(tmp_path, companions=True))

    argvs = _unattended(adapter, tmp_path, launcher)

    assert argvs["nudge"][-1] == "go on"
    assert argvs["judge"][-1] == "judge it"


def test_takeover_command_carries_no_bundle_flags(tmp_path: Path) -> None:
    adapter = _claude(_RecordingLauncher(), bundle=_delivery(tmp_path, companions=True))

    command = adapter.resume_command("/w", "s-1", attended=True)

    assert "--settings" not in command
    assert "--mcp-config" not in command


# --------------------------------------------------------------------------- #
# No `[harness] config_dir`: the launch is the one it always was.


@pytest.mark.parametrize("make", [_claude, _opencode], ids=["claude-code", "opencode"])
def test_argv_and_env_ignore_the_bundle_key(tmp_path: Path, make: Callable[..., Any]) -> None:
    plain = _load(tmp_path, "")
    keyed = _load(tmp_path, f'[harness]\nconfig_dir = "{tmp_path}"\n')
    launches = []
    for config in (plain, keyed):
        launcher = _RecordingLauncher()
        _unattended(make(launcher, autonomy=config.autonomy), tmp_path, launcher)
        launches.append((launcher.argvs, launcher.envs))

    assert launches[0] == launches[1]
    assert len(launches[0][0]) == 4 == len(launches[0][1])
