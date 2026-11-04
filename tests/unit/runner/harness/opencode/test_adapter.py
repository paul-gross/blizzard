"""The OpenCode adapter — command composition (unit) and a real subprocess (component).

Unit tests drive command building and model/effort/compaction resolution against a
monkeypatched ``subprocess.Popen``; component tests launch a fake ``opencode`` binary
(``tests/support_opencode_binary.py``)."""

from __future__ import annotations

import json
import shutil
import subprocess
from concurrent.futures import Executor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.runner.harness.adapter import HarnessSpawnError, ResumeHandle
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.declaration import SharedHarnessInputs
from blizzard.runner.harness.identity import OPENCODE_HARNESS_ID
from blizzard.runner.harness.internal.committed_corpus import CommittedCorpus
from blizzard.runner.harness.internal.process_launcher import ProcessLauncher
from blizzard.runner.harness.json_value import JsonObject
from blizzard.runner.harness.offline_compatibility import admitted_corpus_versions
from blizzard.runner.harness.opencode.adapter import (
    OpenCodeAdapter,
)
from blizzard.runner.harness.opencode.command import OpenCodeCommand, OpenCodeInvocationKind
from blizzard.runner.harness.opencode.compatibility.probe import ADMITTED_OPENCODE_RANGE
from blizzard.runner.harness.opencode.declaration import OPENCODE_DECLARATION
from blizzard.runner.harness.opencode.model_limits import OpenCodeModelLimits
from blizzard.runner.harness.opencode.permissions.permission_resolver import (
    OpenCodeEffectivePermissions,
    OpenCodePermissionResolveError,
    SubprocessOpenCodePermissionResolver,
)
from blizzard.runner.harness.opencode.section import OpenCodeSection
from blizzard.runner.harness.opencode.shapes import OpenCodePermissionRule
from blizzard.runner.harness.opencode.usage.price_cache import (
    FileOpenCodePriceCatalog,
    OpenCodeContextTier,
    OpenCodeModelPrice,
    OpenCodeRate,
)
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.lifecycle.session import HarnessSelection, HarnessSelector, SkippedHarness
from blizzard.runner.node_steps.envelope import GraphArtifact
from blizzard.runner.process.child_env import AllowlistedEnv
from tests.opencode_usage_limit_fixture import USAGE_LIMIT_EVENT
from tests.repo_files import repo_root
from tests.runner_fakes import FakeProbe, make_envelope
from tests.support_child_launch import CHILDREN, version_probe
from tests.support_opencode_adapter import adapter as _adapter
from tests.support_opencode_adapter import preamble as _preamble

# The oldest committed corpus inside the admitted range — stays correct as corpora are added.
_AN_ADMITTED_OPENCODE_VERSION = admitted_corpus_versions(CommittedCorpus(), "opencode", ADMITTED_OPENCODE_RANGE)[0]
_CORPUS_DIR = (
    repo_root() / "src" / "blizzard" / "runner" / "harness" / "contracts" / "opencode" / _AN_ADMITTED_OPENCODE_VERSION
)


def _manifest() -> dict[str, Any]:
    return json.loads((_CORPUS_DIR / "manifest.json").read_text())


def _fixtures() -> list[tuple[str, dict[str, Any]]]:
    manifest = _manifest()
    return [(entry["name"], json.loads((_CORPUS_DIR / entry["path"]).read_text())) for entry in manifest["fixtures"]]


def _fixture(name: str) -> dict[str, Any]:
    return dict(_fixtures())[name]


def _jsonl(events: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(event) for event in events)


def _which_on_usr_bin(binary: str, path: object = None) -> str:
    """Stands in for ``shutil.which``, resolving every binary under ``/usr/bin``."""
    return f"/usr/bin/{binary}"


@pytest.fixture(autouse=True)
def _opencode_resolves_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mirrors ``tests/unit/runner/harness/claude_code/test_adapter.py``'s fixture of the same shape: a unit
    test's fake spawn must not depend on whether ``opencode`` is really installed on this
    machine's ``PATH`` — `ensure_executable`'s `shutil.which` lookup runs before the
    faked ``subprocess.Popen`` ever sees the call."""
    monkeypatch.setattr(shutil, "which", _which_on_usr_bin)


def test_effective_bundle_environment_reaches_identity_and_fallback_launches(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    effective = tmp_path / "effective"
    effective.mkdir()
    content = '{"permission":{"question":"deny"},"plugin":[]}'
    (effective / "opencode.json").write_text(content)
    adapter = _adapter(
        spawn_executor,
        worker_config_path=str(effective / "opencode.json"),
        effective_config_dir=str(effective),
    )
    for env in (adapter.identity_env(_preamble(str(tmp_path)), "ch_1", "ses_1"), adapter.config_env()):
        assert env["OPENCODE_CONFIG"] == str(effective / "opencode.json")
        assert env["OPENCODE_CONFIG_CONTENT"] == content
        assert env["OPENCODE_CONFIG_DIR"] == str(effective)


def test_identity_env_keeps_lease_token_over_allowlisted_daemon_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    monkeypatch.setenv("BLIZZARD_LEASE_TOKEN", "daemon-token")
    config = tmp_path / "opencode.json"
    config.write_text('{"permission":{"question":"deny"},"plugin":[]}')
    adapter = _adapter(
        spawn_executor,
        worker_env=AllowlistedEnv.of(("BLIZZARD_LEASE_TOKEN",)),
        worker_config_path=str(config),
    )
    preamble = replace(_preamble(str(tmp_path)), lease_token="child-token")
    env = adapter.identity_env(preamble, "ch_1", "ses_1")
    assert env["BLIZZARD_LEASE_TOKEN"] == "child-token"
    assert env["OPENCODE_CONFIG_CONTENT"] == config.read_text()


def test_relative_companions_resolve_in_content_from_snapshot_not_worker_cwd(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    effective = tmp_path / "effective"
    (effective / "prompts").mkdir(parents=True)
    (effective / "prompts" / "agent.txt").write_text("snapshot prompt")
    config = effective / "opencode.json"
    document: dict[str, object] = {
        "permission": {"question": "deny"},
        "plugin": [],
        "agent": {"x": {"prompt": "{file:./prompts/agent.txt}"}},
    }
    config.write_text(json.dumps(document))
    adapter = _adapter(spawn_executor, worker_config_path=str(config), effective_config_dir=str(effective))

    child = adapter.identity_env(_preamble(str(tmp_path / "other-cwd")), "ch_1", "ses_1")
    assert child["OPENCODE_CONFIG"] == str(config)
    assert child["OPENCODE_CONFIG_DIR"] == str(effective)
    assert json.loads(child["OPENCODE_CONFIG_CONTENT"])["agent"]["x"]["prompt"] == (
        f"{{file:{effective / 'prompts' / 'agent.txt'}}}"
    )
    assert json.loads(config.read_text()) == document


def test_effective_bundle_is_delivered_to_every_launch_kind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    effective = tmp_path / "effective"
    effective.mkdir()
    config = effective / "opencode.json"
    config.write_text('{"permission":{"question":"deny"},"plugin":[]}')
    workdir = tmp_path / "project"
    workdir.mkdir()
    captured: list[dict[str, str]] = []

    class FakeProcess:
        pid = 9999999

    def launch(cmd: list[str], **kwargs: Any) -> FakeProcess:
        captured.append(kwargs["env"])
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", launch)
    adapter = _adapter(spawn_executor, worker_config_path=str(config), effective_config_dir=str(effective))
    preamble = _preamble(str(workdir), stdout_path=str(tmp_path / "spawn.out"))
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    adapter.spawn(envelope, preamble, None)
    adapter.spawn(envelope, preamble, None, resume_from="ses_prior")
    adapter.resume_with_message(str(workdir), "ses_prior", "continue", preamble=preamble)
    adapter.judge(str(workdir), "ses_prior", "assess", str(tmp_path / "judge.out"), preamble=preamble)
    adapter.resume_with_message(str(workdir), "ses_prior", "continue")
    adapter.judge(str(workdir), "ses_prior", "assess", str(tmp_path / "fallback.out"))

    assert len(captured) == 6
    for env in captured:
        assert env["OPENCODE_CONFIG"] == str(config)
        assert env["OPENCODE_CONFIG_CONTENT"] == config.read_text()
        assert env["OPENCODE_CONFIG_DIR"] == str(effective)


def test_numeric_window_reaches_all_launches_using_session_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    config = tmp_path / "config.json"
    config.write_text('{"permission":{"question":"deny"},"plugin":[]}')
    captured: list[tuple[list[str], dict[str, str]]] = []

    class FakeProcess:
        pid = 9999999

    class Catalog:
        def limits_for(self, provider: str, model: str) -> OpenCodeModelLimits | None:
            return OpenCodeModelLimits(100000, 5000) if (provider, model) == ("old", "model") else None

    class Resolver:
        def resolve(self, *, cwd: str, env: Any) -> OpenCodeEffectivePermissions:
            return OpenCodeEffectivePermissions(merged_config={"compaction": {"reserved": 1200}}, agent_rulesets={})

    def launch(cmd: list[str], **kwargs: Any) -> FakeProcess:
        captured.append((cmd, kwargs["env"]))
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", launch)
    adapter = _adapter(
        spawn_executor,
        model="new/default",
        worker_config_path=str(config),
        limits_catalog=Catalog(),
        permission_resolver=Resolver(),
    )
    preamble = _preamble(str(tmp_path), stdout_path=str(tmp_path / "spawn.out"))
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    adapter.spawn(envelope, preamble, None, model="old/model", compaction_window="8k")
    adapter.spawn(envelope, preamble, None, resume_from="ses_prior", model="old/model", compaction_window="8k")
    adapter.resume_with_message(str(tmp_path), "ses_prior", "continue", model="old/model", compaction_window="8k")
    adapter.judge(
        str(tmp_path), "ses_prior", "assess", str(tmp_path / "judge.out"), model="old/model", compaction_window="8k"
    )
    adapter.spawn(envelope, preamble, None, resume_from="ses_prior", compaction_window="8k")

    for _, env in captured[:4]:
        content = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        assert content["provider"]["old"]["models"]["model"]["limit"]["input"] == 9200
        assert content["permission"]["question"] == "deny"
    assert "--model" in captured[0][0]
    assert all("--model" not in command for command, _ in captured[1:])
    assert captured[4][1]["OPENCODE_CONFIG_CONTENT"] == config.read_text()


@pytest.mark.parametrize("failure", ["missing-model", "missing-limits", "resolver-error", "invalid-effective"])
def test_numeric_window_fallback_preserves_launch_and_warns_once_per_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor, failure: str
) -> None:
    config = tmp_path / "config.json"
    content = '{"permission":{"question":"deny"},"plugin":[],"custom":"retained"}'
    config.write_text(content)
    captured: list[dict[str, str]] = []

    class FakeProcess:
        pid = 9999999

    class Catalog:
        def limits_for(self, provider: str, model: str) -> OpenCodeModelLimits | None:
            return None if failure == "missing-limits" else OpenCodeModelLimits(100000, 5000)

    class Resolver:
        def resolve(self, *, cwd: str, env: Any) -> OpenCodeEffectivePermissions:
            if failure == "resolver-error":
                raise OpenCodePermissionResolveError("config unavailable")
            effective: JsonObject = {"compaction": {"reserved": "invalid"}} if failure == "invalid-effective" else {}
            return OpenCodeEffectivePermissions(merged_config=effective, agent_rulesets={})

    def launch(cmd: list[str], **kwargs: Any) -> FakeProcess:
        captured.append(kwargs["env"])
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", launch)
    adapter = _adapter(
        spawn_executor, worker_config_path=str(config), limits_catalog=Catalog(), permission_resolver=Resolver()
    )
    model = None if failure == "missing-model" else "provider/model"
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    preamble = _preamble(str(tmp_path), stdout_path=str(tmp_path / "spawn.out"))
    with capture_logs() as logs:
        adapter.spawn(envelope, preamble, None, model=model, compaction_window="8k")
        adapter.spawn(envelope, preamble, None, resume_from="ses_prior", model=model, compaction_window="8k")
        adapter.judge(
            str(tmp_path), "ses_prior", "assess", str(tmp_path / "judge.out"), model=model, compaction_window="8k"
        )
        adapter.resume_with_message(str(tmp_path), "ses_prior", "continue", model=model, compaction_window="9k")

    assert len(captured) == 4
    assert all(env["OPENCODE_CONFIG_CONTENT"] == content for env in captured)
    warnings = [
        log for log in logs if log.get("event") == "cannot apply OpenCode compaction window; launching unchanged"
    ]
    expected = [("", "8k"), (None, "8k"), (None, "9k")] if model is None else [(model, "8k"), (model, "9k")]
    assert [(log["model"], log["window"]) for log in warnings] == expected
    assert all(log["log_level"] == "warning" for log in warnings)


def test_production_binding_reads_limits_from_worker_cache_for_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    home = tmp_path / "worker-home"
    cache = home / ".cache"
    catalog = cache / "opencode" / "models.json"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(json.dumps({"provider": {"models": {"model": {"limit": {"context": 100000, "output": 5000}}}}}))
    monkeypatch.setenv("HOME", str(home))
    worker_env = AllowlistedEnv.of(())
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "daemon-cache"))
    config = tmp_path / "config.json"
    config.write_text('{"permission":{"question":"deny"},"plugin":[]}')
    captured: list[dict[str, str]] = []

    class FakeProcess:
        pid = 9999999

    def launch(cmd: list[str], **kwargs: Any) -> FakeProcess:
        captured.append(kwargs["env"])
        return FakeProcess()

    def resolve(self: Any, *, cwd: str, env: Any) -> OpenCodeEffectivePermissions:
        return OpenCodeEffectivePermissions(merged_config={"compaction": {"reserved": 1200}}, agent_rulesets={})

    monkeypatch.setattr(subprocess, "Popen", launch)
    monkeypatch.setattr(SubprocessOpenCodePermissionResolver, "resolve", resolve)
    process = FakeProbe()
    binding = OPENCODE_DECLARATION.binding(
        OpenCodeSection(worker_config_path=str(config)),
        SharedHarnessInputs(
            root=tmp_path, autonomy=Autonomy.Auto, config_dir=None, worker_env=worker_env, transcripts_root=""
        ),
        process=process,
        launcher=ProcessLauncher(process, executor=spawn_executor, children=CHILDREN),
        children=CHILDREN,
    )
    assert binding.adapter is not None
    binding.adapter.resume_with_message(
        str(tmp_path), "ses_prior", "continue", model="provider/model", compaction_window="8k"
    )
    limits = json.loads(captured[0]["OPENCODE_CONFIG_CONTENT"])["provider"]["provider"]["models"]["model"]["limit"]
    assert limits == {"input": 9200, "context": 100000, "output": 5000}


def test_fresh_mint_carries_model_and_format_json_and_auto() -> None:
    cmd = OpenCodeCommand("opencode").build(
        OpenCodeInvocationKind.FRESH, prompt="do the thing", model="openai/gpt-5.6", variant="max", auto=True
    )
    assert cmd == [
        "opencode",
        "run",
        "--format",
        "json",
        "--model",
        "openai/gpt-5.6",
        "--variant",
        "max",
        "--auto",
        "do the thing",
    ]


def test_resume_omits_model_and_carries_session() -> None:
    cmd = OpenCodeCommand("opencode").build(
        OpenCodeInvocationKind.RESUME, prompt="continue", session_id="ses_1", model="openai/gpt-5.6", auto=True
    )
    assert "--model" not in cmd
    assert cmd[cmd.index("--session") + 1] == "ses_1"


def test_judge_and_nudge_compose_the_same_shape_as_resume() -> None:
    """NUDGE serves both a produces-nudge and a parked-answer delivery: the two
    are distinct CALLER intents with no OpenCode CLI-level difference, so there is no
    separate ANSWER kind to also exercise here — NUDGE already covers both."""
    builder = OpenCodeCommand("opencode")
    judge_cmd = builder.build(
        OpenCodeInvocationKind.JUDGE, prompt="assess", session_id="ses_1", variant="max", auto=True
    )
    nudge_cmd = builder.build(
        OpenCodeInvocationKind.NUDGE, prompt="continue", session_id="ses_1", variant="max", auto=True
    )
    for cmd, prompt in ((judge_cmd, "assess"), (nudge_cmd, "continue")):
        assert cmd[:6] == ["opencode", "run", "--format", "json", "--session", "ses_1"]
        assert "--variant" in cmd and cmd[cmd.index("--variant") + 1] == "max"
        assert "--auto" in cmd
        assert cmd[-1] == prompt


def test_takeover_argv_has_no_format_json_and_no_auto() -> None:
    """Interactive takeover has no kind on `OpenCodeInvocationKind` at all — it
    is composed by this wholly separate method, never through `OpenCodeCommand.build`."""
    argv = OpenCodeCommand("opencode").takeover_argv(session_id="ses_1", model="openai/gpt-5.6", variant="max")
    assert argv == ["opencode", "--session", "ses_1", "--model", "openai/gpt-5.6", "--variant", "max"]
    assert "--format" not in argv
    assert "--auto" not in argv


# --------------------------------------------------------------------------- #
# Model / effort / compaction resolution.


def test_resolve_model_strict_maps_a_configured_tier(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, model_aliases=(("blizzard:frontier", "openai/gpt-5.6-luna"),))
    assert adapter.resolve_model_strict(["blizzard:frontier"]) == "openai/gpt-5.6-luna"


def test_resolve_model_strict_returns_none_for_an_unmapped_tier(spawn_executor: Executor) -> None:
    # No built-in OpenCode tiers (unlike Claude Code): an unmapped tier resolves to
    # nothing, which is the contract a multi-harness selection reads.
    adapter = _adapter(spawn_executor)
    assert adapter.resolve_model_strict(["blizzard:frontier"]) is None


def test_resolve_model_falls_back_to_the_adapter_default_when_nothing_resolves(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, model="openai/gpt-5.6-luna")
    assert adapter.resolve_model(["blizzard:frontier"]) == "openai/gpt-5.6-luna"


def test_resolve_model_falls_back_to_empty_string_with_no_configured_default(spawn_executor: Executor) -> None:
    # Empty is legitimate for OpenCode: it lets OpenCode resolve its own configured
    # default rather than Blizzard inventing one.
    adapter = _adapter(spawn_executor)
    assert adapter.resolve_model(["blizzard:frontier"]) == ""


def test_resolve_model_accepts_a_bare_valid_provider_model_reference(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor)
    assert adapter.resolve_model_strict(["openai/gpt-5.6-luna"]) == "openai/gpt-5.6-luna"


def test_resolve_model_skips_a_malformed_native_reference(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, model_aliases=(("blizzard:basic", "openai/gpt-5.6-mini"),))
    # Not `provider/model` shaped (no slash) — this belongs to another harness's own
    # native vocabulary (e.g. Claude Code's bare `opus`), never handed to this CLI.
    assert adapter.resolve_model(["opus", "blizzard:basic"]) == "openai/gpt-5.6-mini"


def test_resolve_effort_passes_through_the_well_known_ordinal(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor)
    assert adapter.resolve_effort("max") == "max"


def test_resolve_effort_maps_a_configured_alias(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, effort_aliases=(("high", "xhigh"),))
    assert adapter.resolve_effort("high") == "xhigh"


def test_resolve_effort_drops_an_unrecognized_value(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor)
    assert adapter.resolve_effort("ludicrous") is None


def test_resolve_effort_of_none_is_none(spawn_executor: Executor) -> None:
    assert _adapter(spawn_executor).resolve_effort(None) is None


def test_resolve_compaction_window(spawn_executor: Executor) -> None:
    # No numeric translation is ever invented, regardless of the value's shape.
    adapter = _adapter(spawn_executor)
    assert adapter.resolve_compaction_window("auto") == "auto"
    assert adapter.resolve_compaction_window("200000") == "200000"
    assert adapter.resolve_compaction_window("8K") == "8K"
    assert adapter.resolve_compaction_window("invalid") is None
    assert adapter.resolve_compaction_window(None) is None


# --------------------------------------------------------------------------- #
# `resolvable_tier_ids`: the capability snapshot's own tier enumeration.


def test_resolvable_tier_ids_is_empty_with_no_config_at_all(spawn_executor: Executor) -> None:
    # No built-in OpenCode tiers (unlike Claude Code's three): with nothing configured,
    # nothing is resolvable.
    adapter = _adapter(spawn_executor)
    assert adapter.resolvable_tier_ids() == ()


def test_resolvable_tier_ids_names_a_configured_alias(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, model_aliases=(("blizzard:frontier", "openai/gpt-5.6-luna"),))
    assert set(adapter.resolvable_tier_ids()) == {"blizzard:frontier"}


def test_resolvable_tier_ids_merges_every_configured_alias_once(spawn_executor: Executor) -> None:
    # Override-by-key precedence, matching `_resolve_one_model`: two distinct aliases
    # both appear, each exactly once.
    adapter = _adapter(
        spawn_executor=spawn_executor,
        model_aliases=(
            ("blizzard:frontier", "openai/gpt-5.6-luna"),
            ("blizzard:basic", "openai/gpt-5.6-mini"),
        ),
    )
    tiers = adapter.resolvable_tier_ids()
    assert tiers.count("blizzard:frontier") == 1
    assert set(tiers) == {"blizzard:frontier", "blizzard:basic"}


# --------------------------------------------------------------------------- #
# Harness selection (harness-selection spec): an unmapped tier is a skip, not a spawn.


def test_harness_selector_skips_opencode_when_its_tier_is_unmapped(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor)  # no model_aliases at all
    registry = HarnessRegistry({OPENCODE_HARNESS_ID: HarnessBinding(adapter=adapter)})
    envelope = make_envelope(
        "ch_1",
        "build",
        node_id="nd_build",
        choices=[("pass", "ok")],
        session_harnesses=[OPENCODE_HARNESS_ID, "some-other-harness"],
        session_model=["blizzard:frontier"],
    )

    selection = HarnessSelector(harnesses=registry).select(envelope.node)

    assert selection == HarnessSelection(
        harness_id=None,
        skipped=(
            SkippedHarness(OPENCODE_HARNESS_ID, "no-authored-tier"),
            SkippedHarness("some-other-harness", "unknown"),
        ),
    )


def test_harness_selector_picks_opencode_once_its_tier_is_mapped(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, model_aliases=(("blizzard:frontier", "openai/gpt-5.6-luna"),))
    registry = HarnessRegistry({OPENCODE_HARNESS_ID: HarnessBinding(adapter=adapter)})
    envelope = make_envelope(
        "ch_1",
        "build",
        node_id="nd_build",
        choices=[("pass", "ok")],
        session_harnesses=[OPENCODE_HARNESS_ID],
        session_model=["blizzard:frontier"],
    )

    selection = HarnessSelector(harnesses=registry).select(envelope.node)

    assert selection == HarnessSelection(harness_id=OPENCODE_HARNESS_ID, skipped=())


# --------------------------------------------------------------------------- #
# `resume_command` (takeover): no `--format json`, no `--auto`, ever.


def test_resume_command_is_the_literal_takeover(spawn_executor: Executor) -> None:
    cmd = _adapter(spawn_executor, binary="opencode").resume_command("/ws/e1", "ses-x")
    assert cmd == "cd /ws/e1 && opencode --session ses-x"


def test_resume_command_carries_model_and_variant(spawn_executor: Executor) -> None:
    cmd = _adapter(spawn_executor, binary="opencode").resume_command(
        "/ws/e1", "ses-x", model="openai/gpt-5.6", effort="max"
    )
    assert cmd == "cd /ws/e1 && opencode --session ses-x --model openai/gpt-5.6 --variant max"


def test_resume_command_is_identical_attended_or_not(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="opencode")
    assert adapter.resume_command("/ws/e1", "ses-x", attended=True) == adapter.resume_command("/ws/e1", "ses-x")


def test_honors_session_hint_is_false(spawn_executor: Executor) -> None:
    assert _adapter(spawn_executor).honors_session_hint() is False


# --------------------------------------------------------------------------- #
# `spawn` requires an injected stdout path (the identity handshake needs somewhere to read).


def test_spawn_without_a_stdout_path_raises(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, binary="opencode")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])
    with pytest.raises(HarnessSpawnError):
        adapter.spawn(envelope, _preamble("/ws/e1"), session_hint="hint")


class _AskingResolver:
    def resolve(self, *, cwd: str, env: Any) -> OpenCodeEffectivePermissions:
        return OpenCodeEffectivePermissions(
            merged_config={}, agent_rulesets={"build": (OpenCodePermissionRule("bash", "*", "ask"),)}
        )


@pytest.mark.parametrize("content", ["[]", '"text"', "3", "null"])
def test_non_object_config_content_fails_the_launch_as_a_spawn_error(spawn_executor: Executor, content: str) -> None:
    adapter = _adapter(spawn_executor, autonomy=Autonomy.Normal, permission_resolver=_AskingResolver())
    with pytest.raises(HarnessSpawnError, match="cannot establish"):
        adapter.deny_unanswerable_asks("/ws/e1", {"OPENCODE_CONFIG_CONTENT": content})


# --------------------------------------------------------------------------- #
# Fresh-session handshake (component): identity arrives on the worker's own stdout.


def _fake_popen_capturing(captured: dict[str, list[str]]) -> object:
    class _FakeSpawnedProcess:
        pid = 9_999_999

    def _fake_popen(cmd: list[str], **kwargs: object) -> _FakeSpawnedProcess:
        captured["cmd"] = cmd
        return _FakeSpawnedProcess()

    return _fake_popen


def test_spawn_pins_the_resolved_model_at_mint_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(spawn_executor, binary="opencode")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    adapter.spawn(
        envelope,
        _preamble(str(workdir), stdout_path=str(stdout_path)),
        session_hint="hint",
        model="openai/gpt-5.6-luna",
    )

    cmd = captured["cmd"]
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "openai/gpt-5.6-luna"
    assert "--session" not in cmd


def test_spawn_with_resume_from_omits_model_and_carries_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    workdir = tmp_path / "e1"
    workdir.mkdir()
    stdout_path = tmp_path / "lease-1.stdout"
    adapter = _adapter(spawn_executor, binary="opencode")
    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    handle = adapter.spawn(
        envelope,
        _preamble(str(workdir), stdout_path=str(stdout_path)),
        session_hint=None,
        resume_from="ses_prior",
        model="openai/gpt-5.6-luna",
    )

    cmd = captured["cmd"]
    assert "--model" not in cmd
    assert cmd[cmd.index("--session") + 1] == "ses_prior"
    assert handle.await_identity(0).session_id == "ses_prior"


def test_resume_with_message_stamps_process_start_time_and_a_real_confirm_durable(
    monkeypatch: pytest.MonkeyPatch,
    spawn_executor: Executor,
) -> None:
    """A resume gets the same ownership a fresh spawn or judge gets — the
    launcher's own recorded start time, and a real disarm signal, not
    `ResumeHandle`'s bare no-op default."""
    captured: dict[str, list[str]] = {}
    monkeypatch.setattr(subprocess, "Popen", _fake_popen_capturing(captured))
    probe = FakeProbe(alive={(9_999_999, "fake-resume-start-time")})
    adapter = OpenCodeAdapter(
        worker_env=AllowlistedEnv.of(()),
        binary="opencode",
        process=probe,
        launcher=ProcessLauncher(probe, executor=spawn_executor, children=CHILDREN),
        versions=version_probe(),
    )

    resumed = adapter.resume_with_message("/ws", "ses_recorded", "continue")

    assert resumed.pid == 9_999_999
    assert resumed.process_start_time == "fake-resume-start-time"
    assert resumed.confirm_durable is not ResumeHandle.__dataclass_fields__["confirm_durable"].default


# --------------------------------------------------------------------------- #
# Output and usage (execution spec, "Output and usage") — driven off the full pinned corpus.

_EXPECTED_VERDICT_AND_ASSESSMENT: dict[str, tuple[str | None, str]] = {
    "success": ("pass", ""),
    "provider_error": (None, "ProviderError: provider request failed (status 503)"),
    "permission_denial": (None, ""),
    "interrupted_tool": (None, ""),
    "compaction": ("pass", ""),
    "child_session": (None, ""),
    "live_success": ("pass", "<Choice>pass</Choice>"),
}

_EXPECTED_USABLE: dict[str, bool] = {
    "success": True,
    "provider_error": False,
    "permission_denial": True,
    "interrupted_tool": False,
    "compaction": True,
    "child_session": True,
    "live_success": True,
}

# (input, output — reasoning folded in, cache_read, cache_create, cost_usd)
_EXPECTED_USAGE: dict[str, tuple[int, int, int, int, float | None] | None] = {
    "success": (120, 45 + 18, 30, 15, 0.0123),
    "provider_error": None,  # no completed step at all
    "permission_denial": (40, 8, 0, 0, None),  # cost 0 reads as unknown, never free
    "interrupted_tool": None,
    "compaction": (200, 25 + 10, 90, 5, None),
    "child_session": (80, 20 + 4, 10, 2, None),
    "live_success": (120 + 30, (45 + 18) + (8 + 0), 30 + 20, 15 + 0, None),  # two completed steps, one turn
}


@pytest.mark.parametrize("name,payload", _fixtures(), ids=lambda item: item if isinstance(item, str) else "fixture")
def test_verdict_and_assessment_match_the_expected_shape_for_every_fixture(
    name: str, payload: dict[str, Any], spawn_executor: Executor
) -> None:
    output = _jsonl(payload["events"])
    adapter = _adapter(spawn_executor)

    expected_verdict, expected_assessment = _EXPECTED_VERDICT_AND_ASSESSMENT[name]
    assert adapter.parse_verdict(output) == expected_verdict
    assert adapter.parse_assessment(output) == expected_assessment


@pytest.mark.parametrize("name,payload", _fixtures(), ids=lambda item: item if isinstance(item, str) else "fixture")
def test_has_usable_output_matches_the_expected_shape_for_every_fixture(
    name: str, payload: dict[str, Any], spawn_executor: Executor
) -> None:
    output = _jsonl(payload["events"])
    assert _adapter(spawn_executor).has_usable_output(output) is _EXPECTED_USABLE[name]


@pytest.mark.parametrize("name,payload", _fixtures(), ids=lambda item: item if isinstance(item, str) else "fixture")
def test_parse_usage_matches_the_expected_shape_for_every_fixture(
    name: str, payload: dict[str, Any], spawn_executor: Executor
) -> None:
    output = _jsonl(payload["events"])
    sample = _adapter(spawn_executor).parse_usage(output, "spawn")

    expected = _EXPECTED_USAGE[name]
    if expected is None:
        assert sample is None
        return
    input_tokens, output_tokens, cache_read_tokens, cache_create_tokens, cost_usd = expected
    assert sample is not None
    assert (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens) == (
        input_tokens,
        output_tokens,
        cache_read_tokens,
        cache_create_tokens,
    )
    assert sample.cost_usd == cost_usd


def test_verdict_body_never_carries_tool_output_or_child_session_text(spawn_executor: Executor) -> None:
    # `child_session`'s tool call and its child's conversation must never leak into the
    # root turn's verdict body — the corpus's sharpest exclusion case.
    payload = _fixture("child_session")
    output = _jsonl(payload["events"])
    adapter = _adapter(spawn_executor)

    assert adapter.parse_verdict(output) is None
    assert adapter.parse_assessment(output) == ""
    # The child's own reply lives only in `child_export`, a document this adapter never
    # reads for verdict/assessment parsing — asserted structurally below.

    for fixture_name, fixture_payload in _fixtures():
        fixture_output = _jsonl(fixture_payload["events"])
        for event in fixture_payload["events"]:
            part: dict[str, Any] = event.get("part") or {}
            state: dict[str, Any] = part.get("state") or {}
            tool_output = state.get("output")
            if tool_output:
                verdict = adapter.parse_verdict(fixture_output) or ""
                assessment = adapter.parse_assessment(fixture_output)
                assert tool_output not in verdict, fixture_name
                assert tool_output not in assessment, fixture_name


def test_parse_usage_and_has_usable_output_tolerate_malformed_capture(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor)
    assert adapter.parse_verdict("not json at all") is None
    assert adapter.parse_assessment("not json at all") == ""
    assert adapter.has_usable_output("not json at all") is False
    assert adapter.parse_usage("not json at all", "spawn") is None


def test_parse_events_skips_one_malformed_trailing_line_and_keeps_the_rest(spawn_executor: Executor) -> None:
    """A killed-mid-write worker can leave one truncated line behind an otherwise-complete
    capture; OpenCode has no transcript fallback to re-derive a lost verdict from, so
    skipping just that one bad line is the entire tolerance this binding can offer."""
    payload = _fixture("success")
    output = _jsonl(payload["events"]) + "\nnot json at all, and truncated besides"
    adapter = _adapter(spawn_executor)

    assert adapter.parse_verdict(output) == "pass"
    assert adapter.has_usable_output(output) is True
    sample = adapter.parse_usage(output, "spawn")
    assert sample is not None
    assert (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens) == (
        120,
        45 + 18,
        30,
        15,
    )
    assert sample.cost_usd == 0.0123


def test_sum_transcript_usage_dedups_a_step_described_by_both_event_and_exported_message(
    spawn_executor: Executor,
) -> None:
    # `child_session` exercises this: the root's one completed step ("prt_child_finish")
    # is described both by the process's stdout event and the exported message.
    payload = _fixture("child_session")
    event_lines = [json.dumps(event) for event in payload["events"]]
    message_lines = [json.dumps(message) for message in payload["export"]["messages"]]
    adapter = _adapter(spawn_executor)

    events_only = adapter.sum_transcript_usage(event_lines, "spawn")
    mixed = adapter.sum_transcript_usage(event_lines + message_lines, "spawn")

    expected = (80, 20 + 4, 10, 2)
    assert (
        events_only.input_tokens,
        events_only.output_tokens,
        events_only.cache_read_tokens,
        events_only.cache_create_tokens,
    ) == expected
    assert (mixed.input_tokens, mixed.output_tokens, mixed.cache_read_tokens, mixed.cache_create_tokens) == expected
    # A transcript never carries a dollar figure, regardless of what the events themselves reported.
    assert mixed.cost_usd is None


def test_sum_transcript_usage_skips_tokenless_user_messages_without_raising(spawn_executor: Executor) -> None:
    payload = _fixture("success")
    lines = [json.dumps(message) for message in payload["export"]["messages"]]  # includes the user message

    sample = _adapter(spawn_executor).sum_transcript_usage(lines, "spawn")

    assert (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens) == (
        120,
        45 + 18,
        30,
        15,
    )
    assert sample.cost_usd is None


def test_sum_transcript_usage_recovers_step_finish_from_a_message_with_an_empty_tool_title(
    spawn_executor: Executor,
) -> None:
    # A tool that matched nothing reports `"title": ""`; that part must not cost its message's usage.
    message = _fixture("success")["export"]["messages"][1]
    message["parts"] = [part for part in message["parts"] if part["id"] in ("prt_success_glob", "prt_success_finish")]
    assert [part["type"] for part in message["parts"]] == ["tool", "step-finish"]
    lines = [json.dumps(message)]

    sample = _adapter(spawn_executor).sum_transcript_usage(lines, "spawn")

    assert (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens) == (
        120,
        45 + 18,
        30,
        15,
    )
    assert sample.cost_usd is None


def test_sum_transcript_usage_ignores_unparseable_lines(spawn_executor: Executor) -> None:
    counted = {"input": 5, "output": 2, "reasoning": 1, "cache": {"read": 3, "write": 4}}
    event_line = json.dumps(
        _step_finish_event(session_id="ses_ev", part_id="prt_ev", message_id="msg_ev", cost=0.0, tokens=counted)
    )
    sample = _adapter(spawn_executor).sum_transcript_usage(
        ["", "not json", "{}", '{"type": "future_event", "sessionID": "x"}', event_line], "spawn"
    )

    # Each unparseable line is passed over, never ending the read before the step after it.
    assert (sample.input_tokens, sample.output_tokens, sample.cache_read_tokens, sample.cache_create_tokens) == (
        5,
        2 + 1,
        3,
        4,
    )
    assert sample.cost_usd is None


# --- OpenCode price estimation ----------------------------------------------

# `gpt-5.6-luna`'s base rates (the issue's worked example), dollars per 1,000,000 tokens.
_LUNA_RATE = OpenCodeRate(input=0.20, output=1.20, cache_read=0.02, cache_write=0.25)
_LUNA_TIER = OpenCodeContextTier(
    size=100, rate=OpenCodeRate(input=0.40, output=1.80, cache_read=0.04, cache_write=0.50)
)


@dataclass(frozen=True)
class _FakePriceCatalog:
    """A price catalog pinned to exactly the entries a test names — never file I/O."""

    prices: dict[tuple[str, str], OpenCodeModelPrice]

    def price_for(self, provider: str, model: str) -> OpenCodeModelPrice | None:
        return self.prices.get((provider, model))


def _luna_catalog(**price_kwargs: Any) -> _FakePriceCatalog:
    return _FakePriceCatalog({("openai", "gpt-5.6-luna"): OpenCodeModelPrice(base=_LUNA_RATE, **price_kwargs)})


def _step_finish_event(
    *, session_id: str, part_id: str, message_id: str, cost: float, tokens: dict[str, Any]
) -> dict[str, Any]:
    return {
        "type": "step_finish",
        "sessionID": session_id,
        "part": {
            "id": part_id,
            "sessionID": session_id,
            "messageID": message_id,
            "type": "step-finish",
            "reason": "stop",
            "cost": cost,
            "tokens": tokens,
        },
    }


def _export_message(
    *,
    session_id: str,
    message_id: str,
    provider_id: str,
    model_id: str,
    part_id: str,
    cost: float,
    tokens: dict[str, Any],
) -> dict[str, Any]:
    return {
        "info": {
            "id": message_id,
            "sessionID": session_id,
            "role": "assistant",
            "providerID": provider_id,
            "modelID": model_id,
        },
        "parts": [
            {
                "id": part_id,
                "sessionID": session_id,
                "messageID": message_id,
                "type": "step-finish",
                "reason": "stop",
                "cost": cost,
                "tokens": tokens,
            }
        ],
    }


_ZERO_TOKENS = {"input": 40, "output": 8, "reasoning": 0, "cache": {"read": 0, "write": 0}}


def test_parse_usage_estimates_an_all_zero_cost_step_when_its_model_is_priced(spawn_executor: Executor) -> None:
    payload = _fixture("permission_denial")
    output = _jsonl(payload["events"])
    adapter = _adapter(spawn_executor, model="openai/gpt-5.6-luna", price_catalog=_luna_catalog())

    sample = adapter.parse_usage(output, "spawn")

    assert sample is not None
    assert sample.cost_usd is None
    assert sample.estimated_cost_usd == pytest.approx((40 * 0.20 + 8 * 1.20) / 1_000_000)


def test_parse_usage_leaves_a_non_zero_cost_step_billed_with_no_estimate(spawn_executor: Executor) -> None:
    payload = _fixture("success")
    output = _jsonl(payload["events"])
    adapter = _adapter(spawn_executor, model="openai/gpt-5.6-luna", price_catalog=_luna_catalog())

    sample = adapter.parse_usage(output, "spawn")

    assert sample is not None
    assert sample.cost_usd == pytest.approx(0.0123)
    assert sample.estimated_cost_usd is None


def test_parse_usage_reports_both_a_billed_and_an_estimated_amount_for_a_mixed_invocation(
    spawn_executor: Executor,
) -> None:
    events = [
        _step_finish_event(
            session_id="ses_mixed",
            part_id="prt_billed",
            message_id="msg_billed",
            cost=0.05,
            tokens={"input": 1_000, "output": 200, "reasoning": 0, "cache": {"read": 0, "write": 0}},
        ),
        _step_finish_event(
            session_id="ses_mixed", part_id="prt_zero", message_id="msg_zero", cost=0.0, tokens=_ZERO_TOKENS
        ),
    ]
    adapter = _adapter(spawn_executor, model="openai/gpt-5.6-luna", price_catalog=_luna_catalog())

    sample = adapter.parse_usage(_jsonl(events), "spawn")

    assert sample is not None
    assert sample.cost_usd == pytest.approx(0.05)
    assert sample.estimated_cost_usd == pytest.approx((40 * 0.20 + 8 * 1.20) / 1_000_000)


def test_parse_usage_prices_two_steps_on_either_side_of_a_tier_threshold_at_their_own_rate(
    spawn_executor: Executor,
) -> None:
    # F = input + cache.read + cache.write: 40 stays under the 100 tier, 150 clears it.
    below = {"input": 40, "output": 10, "reasoning": 0, "cache": {"read": 0, "write": 0}}
    above = {"input": 150, "output": 10, "reasoning": 0, "cache": {"read": 0, "write": 0}}
    events = [
        _step_finish_event(session_id="ses_tier", part_id="prt_below", message_id="msg_below", cost=0.0, tokens=below),
        _step_finish_event(session_id="ses_tier", part_id="prt_above", message_id="msg_above", cost=0.0, tokens=above),
    ]
    adapter = _adapter(
        spawn_executor=spawn_executor, model="openai/gpt-5.6-luna", price_catalog=_luna_catalog(tiers=(_LUNA_TIER,))
    )

    sample = adapter.parse_usage(_jsonl(events), "spawn")

    below_expected = (40 * 0.20 + 10 * 1.20) / 1_000_000
    above_expected = (150 * 0.40 + 10 * 1.80) / 1_000_000
    assert sample is not None
    assert sample.estimated_cost_usd == pytest.approx(below_expected + above_expected)
    assert below_expected != above_expected  # each step really did price at its own rate


def test_parse_usage_gives_no_estimate_when_the_invocation_model_does_not_parse(spawn_executor: Executor) -> None:
    payload = _fixture("permission_denial")
    output = _jsonl(payload["events"])
    # No slash: not a valid `provider/model` reference, so the fallback label is unknown.
    adapter = _adapter(
        spawn_executor=spawn_executor, model="not-a-valid-model-reference", price_catalog=_luna_catalog()
    )

    sample = adapter.parse_usage(output, "spawn")

    assert sample is not None
    assert sample.estimated_cost_usd is None


def test_parse_usage_gives_no_estimate_with_no_catalog_injected(spawn_executor: Executor) -> None:
    payload = _fixture("permission_denial")
    output = _jsonl(payload["events"])
    adapter = _adapter(spawn_executor, model="openai/gpt-5.6-luna")  # no price_catalog kwarg at all

    sample = adapter.parse_usage(output, "spawn")

    assert sample is not None
    assert sample.estimated_cost_usd is None


def test_parse_usage_leaves_a_zero_cost_step_unknown_when_the_cache_file_is_missing(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    payload = _fixture("permission_denial")
    output = _jsonl(payload["events"])
    catalog = FileOpenCodePriceCatalog(tmp_path / "does-not-exist" / "models.json")
    adapter = _adapter(spawn_executor, model="openai/gpt-5.6-luna", price_catalog=catalog)

    sample = adapter.parse_usage(output, "spawn")

    assert sample is not None
    assert sample.cost_usd is None
    assert sample.estimated_cost_usd is None


def test_sum_transcript_usage_estimates_a_zero_cost_step_from_its_own_export_message_model(
    spawn_executor: Executor,
) -> None:
    # The invocation's own model resolves to a provider the catalog has no entry for — the
    # estimate can only have come from the exported message's own providerID/modelID.
    line = json.dumps(
        _export_message(
            session_id="ses_export",
            message_id="msg_export",
            provider_id="openai",
            model_id="gpt-5.6-luna",
            part_id="prt_export",
            cost=0.0,
            tokens=_ZERO_TOKENS,
        )
    )
    adapter = _adapter(spawn_executor, model="anthropic/claude-unpriced", price_catalog=_luna_catalog())

    sample = adapter.sum_transcript_usage([line], "spawn")

    assert sample.cost_usd is None
    assert sample.estimated_cost_usd == pytest.approx((40 * 0.20 + 8 * 1.20) / 1_000_000)


def test_sum_transcript_usage_never_bills_but_may_still_carry_an_estimate(spawn_executor: Executor) -> None:
    line = json.dumps(
        _export_message(
            session_id="ses_export",
            message_id="msg_export",
            provider_id="openai",
            model_id="gpt-5.6-luna",
            part_id="prt_export",
            cost=0.0,
            tokens=_ZERO_TOKENS,
        )
    )
    adapter = _adapter(spawn_executor, model="openai/gpt-5.6-luna", price_catalog=_luna_catalog())

    sample = adapter.sum_transcript_usage([line], "spawn")

    assert sample.cost_usd is None
    assert sample.estimated_cost_usd is not None


def test_sum_transcript_usage_drops_the_estimate_entirely_when_any_step_reported_a_real_cost(
    spawn_executor: Executor,
) -> None:
    """A dropped billed amount is never hidden behind an estimate: once one step's
    real cost is unrecoverable on this fallback path, the whole sample stays without one,
    even though another step in the same transcript was priceable."""
    billed_line = json.dumps(
        _export_message(
            session_id="ses_export",
            message_id="msg_billed",
            provider_id="openai",
            model_id="gpt-5.6-luna",
            part_id="prt_billed",
            cost=0.05,
            tokens={"input": 1_000, "output": 200, "reasoning": 0, "cache": {"read": 0, "write": 0}},
        )
    )
    zero_line = json.dumps(
        _export_message(
            session_id="ses_export",
            message_id="msg_zero",
            provider_id="openai",
            model_id="gpt-5.6-luna",
            part_id="prt_zero",
            cost=0.0,
            tokens=_ZERO_TOKENS,
        )
    )
    adapter = _adapter(spawn_executor, model="openai/gpt-5.6-luna", price_catalog=_luna_catalog())

    sample = adapter.sum_transcript_usage([billed_line, zero_line], "spawn")

    assert sample.cost_usd is None
    assert sample.estimated_cost_usd is None


def test_sum_transcript_usage_gives_no_estimate_when_one_zero_cost_steps_model_is_unpriced(
    spawn_executor: Executor,
) -> None:
    """A partial estimate is never reported as complete: one step priced, another whose
    own exported model the catalog has no entry for, leaves the whole sample without one."""
    priced_line = json.dumps(
        _export_message(
            session_id="ses_mixed",
            message_id="msg_priced",
            provider_id="openai",
            model_id="gpt-5.6-luna",
            part_id="prt_priced",
            cost=0.0,
            tokens=_ZERO_TOKENS,
        )
    )
    unpriced_line = json.dumps(
        _export_message(
            session_id="ses_mixed",
            message_id="msg_unpriced",
            provider_id="anthropic",
            model_id="claude-unpriced",
            part_id="prt_unpriced",
            cost=0.0,
            tokens=_ZERO_TOKENS,
        )
    )
    adapter = _adapter(spawn_executor, model="openai/gpt-5.6-luna", price_catalog=_luna_catalog())

    sample = adapter.sum_transcript_usage([priced_line, unpriced_line], "spawn")

    assert sample.cost_usd is None
    assert sample.estimated_cost_usd is None


def test_sum_transcript_usage_keeps_an_export_lines_model_when_a_run_event_repeats_the_step(
    spawn_executor: Executor,
) -> None:
    # One step as an export naming its priced model, then as a run event naming none;
    # the invocation's own model is unpriced, so only the export's pair can price it.
    export_line = json.dumps(
        _export_message(
            session_id="ses_dup",
            message_id="msg_dup",
            provider_id="openai",
            model_id="gpt-5.6-luna",
            part_id="prt_dup",
            cost=0.0,
            tokens=_ZERO_TOKENS,
        )
    )
    event_line = json.dumps(
        _step_finish_event(session_id="ses_dup", part_id="prt_dup", message_id="msg_dup", cost=0.0, tokens=_ZERO_TOKENS)
    )
    adapter = _adapter(spawn_executor, model="anthropic/claude-unpriced", price_catalog=_luna_catalog())

    sample = adapter.sum_transcript_usage([export_line, event_line], "spawn")

    assert sample.estimated_cost_usd == pytest.approx((40 * 0.20 + 8 * 1.20) / 1_000_000)


def test_sum_transcript_usage_records_the_exports_model_over_a_passed_in_or_configured_one(
    spawn_executor: Executor,
) -> None:
    line = json.dumps(
        _export_message(
            session_id="ses_obs",
            message_id="msg_obs",
            provider_id="openai",
            model_id="gpt-5.6-luna",
            part_id="prt_obs",
            cost=0.0,
            tokens=_ZERO_TOKENS,
        )
    )
    adapter = _adapter(spawn_executor, model="anthropic/claude-configured")

    assert adapter.sum_transcript_usage([line], "spawn", model="anthropic/claude-passed").model == "openai/gpt-5.6-luna"
    assert adapter.sum_transcript_usage([line], "spawn").model == "openai/gpt-5.6-luna"


def test_sum_transcript_usage_takes_a_supplied_observation_over_the_exports_model(spawn_executor: Executor) -> None:
    line = json.dumps(
        _export_message(
            session_id="ses_obs",
            message_id="msg_obs",
            provider_id="openai",
            model_id="gpt-5.6-luna",
            part_id="prt_obs",
            cost=0.0,
            tokens=_ZERO_TOKENS,
        )
    )
    adapter = _adapter(spawn_executor, model="anthropic/claude-configured")

    sample = adapter.sum_transcript_usage([line], "judge", model="anthropic/alias", observed="openai/gpt-5.6-sol")

    assert sample.model == "openai/gpt-5.6-sol"
    assert sample.kind == "judge"


def test_sum_transcript_usage_prices_a_model_less_step_at_the_passed_model(spawn_executor: Executor) -> None:
    event_line = json.dumps(
        _step_finish_event(session_id="ses_ev", part_id="prt_ev", message_id="msg_ev", cost=0.0, tokens=_ZERO_TOKENS)
    )
    adapter = _adapter(spawn_executor, price_catalog=_luna_catalog())

    sample = adapter.sum_transcript_usage([event_line], "spawn", model="openai/gpt-5.6-luna")

    assert sample.estimated_cost_usd == pytest.approx((40 * 0.20 + 8 * 1.20) / 1_000_000)


def test_sum_transcript_usage_keeps_a_known_steps_model_when_a_later_copy_names_only_its_provider(
    spawn_executor: Executor,
) -> None:
    named = _export_message(
        session_id="ses_obs",
        message_id="msg_obs",
        provider_id="openai",
        model_id="gpt-5.6-luna",
        part_id="prt_obs",
        cost=0.0,
        tokens=_ZERO_TOKENS,
    )
    provider_only = json.loads(json.dumps(named))
    del provider_only["info"]["modelID"]
    adapter = _adapter(spawn_executor, price_catalog=_luna_catalog())

    sample = adapter.sum_transcript_usage([json.dumps(named), json.dumps(provider_only)], "spawn")

    # A half-named copy is a copy with no model: the step keeps the one its export named.
    assert sample.estimated_cost_usd == pytest.approx((40 * 0.20 + 8 * 1.20) / 1_000_000)


def test_sum_transcript_usage_derives_the_observation_when_none_is_supplied_even_for_a_pinned_model(
    spawn_executor: Executor,
) -> None:
    line = json.dumps(
        _export_message(
            session_id="ses_obs",
            message_id="msg_obs",
            provider_id="openai",
            model_id="gpt-5.6-luna",
            part_id="prt_obs",
            cost=0.0,
            tokens=_ZERO_TOKENS,
        )
    )
    event_line = json.dumps(
        _step_finish_event(session_id="ses_ev", part_id="prt_ev", message_id="msg_ev", cost=0.0, tokens=_ZERO_TOKENS)
    )
    adapter = _adapter(spawn_executor, model="anthropic/claude-configured")

    # The export's own model, never the pinned alias, and a later model-less event never erases it.
    assert adapter.sum_transcript_usage([line, event_line], "spawn", model="anthropic/alias").model == (
        "openai/gpt-5.6-luna"
    )


def test_sum_transcript_usage_keeps_the_fallback_chain_when_no_export_names_a_model(spawn_executor: Executor) -> None:
    event_line = json.dumps(
        _step_finish_event(session_id="ses_ev", part_id="prt_ev", message_id="msg_ev", cost=0.0, tokens=_ZERO_TOKENS)
    )

    assert (
        _adapter(spawn_executor, model="anthropic/c").sum_transcript_usage([event_line], "spawn", model="p/m").model
        == "p/m"
    )
    assert (
        _adapter(spawn_executor, model="anthropic/c").sum_transcript_usage([event_line], "spawn").model == "anthropic/c"
    )
    assert _adapter(spawn_executor).sum_transcript_usage([event_line], "spawn").model == "opencode"


def test_parse_usage_over_run_events_keeps_the_fallback_chain_since_run_events_carry_no_model(
    spawn_executor: Executor,
) -> None:
    """A run event names no provider/model (only an export does), so `parse_usage` has
    nothing of its own to observe: the passed-in model, then the configured one, then the
    bare harness label — and, with no model at all, no invented estimate and no raise."""
    output = _jsonl(
        [_step_finish_event(session_id="ses_ev", part_id="prt_ev", message_id="msg_ev", cost=0.0, tokens=_ZERO_TOKENS)]
    )

    passed = _adapter(spawn_executor, model="anthropic/c", price_catalog=_luna_catalog()).parse_usage(
        output, "spawn", model="openai/gpt-5.6-luna"
    )
    assert passed is not None
    assert passed.model == "openai/gpt-5.6-luna"
    assert passed.estimated_cost_usd == pytest.approx((40 * 0.20 + 8 * 1.20) / 1_000_000)

    configured = _adapter(
        spawn_executor=spawn_executor, model="anthropic/c", price_catalog=_luna_catalog()
    ).parse_usage(output, "spawn")
    assert configured is not None
    assert configured.model == "anthropic/c"

    bare = _adapter(spawn_executor, price_catalog=_luna_catalog()).parse_usage(output, "spawn")
    assert bare is not None
    assert bare.model == "opencode"
    assert bare.estimated_cost_usd is None
    assert bare.input_tokens == 40


def test_observed_model_reads_the_exports_provider_and_model_as_one_reference(spawn_executor: Executor) -> None:
    line = json.dumps(
        _export_message(
            session_id="ses_obs",
            message_id="msg_obs",
            provider_id="openai",
            model_id="gpt-5.6-luna",
            part_id="prt_obs",
            cost=0.0,
            tokens=_ZERO_TOKENS,
        )
    )

    assert _adapter(spawn_executor, model="anthropic/claude-configured").observed_model([line]) == "openai/gpt-5.6-luna"


def test_observed_model_is_none_for_run_events_and_model_less_exports(spawn_executor: Executor) -> None:
    """Never the configured default — a run event names nothing, and an export whose
    `message.info` leaves the pair unset names nothing either."""
    model_less = _export_message(
        session_id="ses_none",
        message_id="msg_none",
        provider_id="openai",
        model_id="gpt-5.6-luna",
        part_id="prt_none",
        cost=0.0,
        tokens=_ZERO_TOKENS,
    )
    del model_less["info"]["providerID"]
    del model_less["info"]["modelID"]
    lines = [
        "",
        "not json",
        json.dumps([1, 2]),
        json.dumps(
            _step_finish_event(
                session_id="ses_ev", part_id="prt_ev", message_id="msg_ev", cost=0.0, tokens=_ZERO_TOKENS
            )
        ),
        json.dumps(model_less),
    ]
    adapter = _adapter(spawn_executor, model="anthropic/claude-configured")

    assert adapter.observed_model(lines) is None
    assert adapter.observed_model([]) is None


@dataclass
class _CountingPriceCatalog:
    inner: _FakePriceCatalog
    lookups: int = 0

    def price_for(self, provider: str, model: str) -> OpenCodeModelPrice | None:
        self.lookups += 1
        return self.inner.price_for(provider, model)


def test_parse_usage_looks_a_model_up_once_however_many_zero_cost_steps_it_prices(spawn_executor: Executor) -> None:
    events = [
        _step_finish_event(
            session_id="ses_memo", part_id=f"prt_{i}", message_id=f"msg_{i}", cost=0.0, tokens=_ZERO_TOKENS
        )
        for i in range(5)
    ]
    catalog = _CountingPriceCatalog(_luna_catalog())
    adapter = _adapter(spawn_executor, model="openai/gpt-5.6-luna", price_catalog=catalog)

    sample = adapter.parse_usage(_jsonl(events), "spawn")

    assert sample is not None
    assert sample.estimated_cost_usd == pytest.approx(5 * (40 * 0.20 + 8 * 1.20) / 1_000_000)
    assert catalog.lookups == 1


# --- classify_usage_limit ------------------------------------


def test_classify_usage_limit_reads_the_captured_go_upsell_event(spawn_executor: Executor) -> None:
    now = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)
    output = json.dumps(USAGE_LIMIT_EVENT)

    limit = _adapter(spawn_executor).classify_usage_limit(output, [], now)

    assert limit is not None
    assert "usage limit" in limit.detail.lower()
    assert limit.resets_at == now + timedelta(hours=2)


def test_classify_usage_limit_is_none_for_an_ordinary_provider_error(spawn_executor: Executor) -> None:
    now = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)
    payload = _fixture("provider_error")
    output = _jsonl(payload["events"])

    assert _adapter(spawn_executor).classify_usage_limit(output, [], now) is None


def test_classify_usage_limit_is_none_for_a_429_that_does_not_name_a_usage_limit(spawn_executor: Executor) -> None:
    now = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)
    output = json.dumps(
        {
            "type": "error",
            "sessionID": "ses_x",
            "error": {"name": "AI_APICallError", "data": {"message": "Too many requests", "statusCode": 429}},
        }
    )

    assert _adapter(spawn_executor).classify_usage_limit(output, [], now) is None


def test_classify_usage_limit_none_reset_for_an_unparseable_duration_never_raises(spawn_executor: Executor) -> None:
    now = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)
    output = json.dumps(
        {
            "type": "error",
            "sessionID": "ses_x",
            "error": {"name": "AI_APICallError", "data": {"message": "Usage limit reached.", "statusCode": 429}},
        }
    )

    limit = _adapter(spawn_executor).classify_usage_limit(output, [], now)

    assert limit is not None
    assert limit.resets_at is None


def test_classify_usage_limit_tolerates_malformed_capture(spawn_executor: Executor) -> None:
    now = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)
    assert _adapter(spawn_executor).classify_usage_limit("not json at all", [], now) is None


# --- classify_provider_overload ------------------------------


def test_classify_provider_overload_reads_a_529_status_code(spawn_executor: Executor) -> None:
    output = json.dumps(
        {
            "type": "error",
            "sessionID": "ses_x",
            "error": {"name": "AI_APICallError", "data": {"message": "Overloaded", "statusCode": 529}},
        }
    )

    overload = _adapter(spawn_executor).classify_provider_overload(output, [])

    assert overload is not None
    assert overload.detail == "Overloaded"


def test_classify_provider_overload_reads_an_overloaded_error_name_without_a_529_status(
    spawn_executor: Executor,
) -> None:
    output = json.dumps(
        {
            "type": "error",
            "sessionID": "ses_x",
            "error": {"name": "OverloadedError", "data": {"message": "the model is overloaded"}},
        }
    )

    assert _adapter(spawn_executor).classify_provider_overload(output, []) is not None


def test_classify_provider_overload_never_calls_the_transcript_reader(spawn_executor: Executor) -> None:
    output = json.dumps(
        {
            "type": "error",
            "sessionID": "ses_x",
            "error": {"name": "AI_APICallError", "data": {"message": "Overloaded", "statusCode": 529}},
        }
    )

    class _ExplodingLines(list[str]):
        def __iter__(self) -> Any:
            raise AssertionError("classify_provider_overload must not read the transcript range")

    overload = _adapter(spawn_executor).classify_provider_overload(output, _ExplodingLines())

    assert overload is not None


def test_classify_provider_overload_is_none_for_an_ordinary_provider_error(spawn_executor: Executor) -> None:
    payload = _fixture("provider_error")
    output = _jsonl(payload["events"])

    assert _adapter(spawn_executor).classify_provider_overload(output, []) is None


def test_classify_provider_overload_is_none_for_a_429_usage_limit_refusal(spawn_executor: Executor) -> None:
    output = json.dumps(USAGE_LIMIT_EVENT)

    assert _adapter(spawn_executor).classify_provider_overload(output, []) is None


def test_classify_provider_overload_tolerates_malformed_capture(spawn_executor: Executor) -> None:
    assert _adapter(spawn_executor).classify_provider_overload("not json at all", []) is None


def test_spawn_never_splices_graph_artifact_content_into_the_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    workdir = tmp_path / "project"
    workdir.mkdir()
    captured: list[list[str]] = []

    class FakeProcess:
        pid = 9999999

    def launch(cmd: list[str], **kwargs: Any) -> FakeProcess:
        captured.append(cmd)
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", launch)
    envelope = make_envelope(
        "ch_1",
        "build",
        node_id="nd_build",
        choices=[("pass", "ok")],
        graph_artifacts=[GraphArtifact(name="rubric", kind=ArtifactKind.ASSET, content="RUBRIC-BODY-7f3a")],
    )

    _adapter(spawn_executor).spawn(envelope, _preamble(str(workdir), stdout_path=str(tmp_path / "spawn.out")), None)

    (cmd,) = captured
    assert envelope.prompt in cmd
    assert not any("RUBRIC-BODY-7f3a" in part or "rubric" in part for part in cmd)


def test_a_declared_supported_list_refuses_a_provider_model_outside_it(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor, supported_models=["openai/*"])
    assert adapter.resolve_model_strict(["anthropic/claude-sonnet-4"]) is None
    assert adapter.resolve_model_strict(["anthropic/claude-sonnet-4", "openai/gpt-5"]) == "openai/gpt-5"


def test_an_empty_supported_list_accepts_no_native_name_but_still_maps_a_tier(spawn_executor: Executor) -> None:
    adapter = _adapter(
        spawn_executor, supported_models=[], model_aliases=[("blizzard:frontier", "openai/gpt-5.6-luna")]
    )
    assert adapter.resolve_model_strict(["openai/gpt-5"]) is None
    assert adapter.resolve_model_strict(["blizzard:frontier"]) == "openai/gpt-5.6-luna"


def test_with_no_declared_list_any_provider_model_is_accepted_and_reported(spawn_executor: Executor) -> None:
    adapter = _adapter(spawn_executor)
    assert adapter.resolve_model_strict(["openai/gpt-5.6-luna"]) == "openai/gpt-5.6-luna"
    assert adapter.resolve_model_strict(["opus"]) is None
    assert adapter.supported_models() == ("*/*",)
