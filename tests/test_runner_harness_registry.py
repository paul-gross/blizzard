"""Harness identity and exact-registry contracts (unit)."""

from __future__ import annotations

from concurrent.futures import Executor
from pathlib import Path
from typing import cast

import pytest

from blizzard.runner.app import create_app_for_export
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.health import HarnessHealthResult
from blizzard.runner.harness.health_cache import HarnessHealthCache
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, OPENCODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.internal.harness_registry import (
    build_production_harness_health_probes,
    build_production_harness_registry,
)
from blizzard.runner.harness.internal.opencode_health import OpenCodeHealthProbe
from blizzard.runner.harness.internal.opencode_price_cache import FileOpenCodePriceCatalog
from blizzard.runner.harness.internal.opencode_transcript_source import OpenCodeTranscriptSource
from blizzard.runner.harness.registry import (
    HarnessBinding,
    HarnessRegistry,
    UnavailableHarnessError,
    UnknownHarnessError,
)
from blizzard.runner.loop.capability_snapshot import capability_snapshot, default_harness_id
from blizzard.runner.loop.process import LinuxProcessProbe
from tests.harness_sections import with_claude_code, with_opencode
from tests.runner_fakes import FakeHarness, FakeTranscriptSource


def _harness() -> FakeHarness:
    return FakeHarness(
        handle=WorkerHandle(session_id="session", pid=1, process_start_time="start", pgid=1), verdict=None
    )


@pytest.mark.unit
def test_session_reference_requires_both_owner_and_raw_session_id() -> None:
    assert SessionReference(CLAUDE_CODE_HARNESS_ID, "session") == SessionReference("claude_code", "session")

    with pytest.raises(ValueError, match="harness id"):
        SessionReference("", "session")
    with pytest.raises(ValueError, match="session id"):
        SessionReference(CLAUDE_CODE_HARNESS_ID, "")


@pytest.mark.unit
def test_registry_resolves_only_the_exact_requested_owner() -> None:
    adapter = _harness()
    source = FakeTranscriptSource()
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=adapter, transcript_source=source)})

    assert registry.lifecycle(CLAUDE_CODE_HARNESS_ID) is adapter
    assert registry.transcript_source(CLAUDE_CODE_HARNESS_ID) is source
    with pytest.raises(UnknownHarnessError) as raised:
        registry.lifecycle("other")
    assert raised.value.known == (CLAUDE_CODE_HARNESS_ID,)


_ROLE_ACCESSORS = (
    "lifecycle",
    "lifecycle_and_verdict",
    "self_test",
    "model_resolution",
    "usage_accounting",
    "usage_limits",
    "provider_overload",
)


@pytest.mark.unit
@pytest.mark.parametrize("accessor", _ROLE_ACCESSORS)
def test_every_role_accessor_resolves_the_bound_adapter_and_raises_the_registry_errors_naming_the_owner(
    accessor: str,
) -> None:
    adapter = _harness()
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=adapter)})
    unbound = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding()})

    assert getattr(registry, accessor)(CLAUDE_CODE_HARNESS_ID) is adapter
    with pytest.raises(UnknownHarnessError):
        getattr(registry, accessor)("other")
    with pytest.raises(UnavailableHarnessError) as raised:
        getattr(unbound, accessor)(CLAUDE_CODE_HARNESS_ID)
    assert raised.value.harness_id == CLAUDE_CODE_HARNESS_ID
    assert raised.value.capability == "adapter"


class _RecordingHealth:
    def __init__(self) -> None:
        self.refreshed: list[tuple[str, object, str | None]] = []

    def refresh(self, harness_id: str, *, adapter: object, observed_version: str | None) -> HarnessHealthResult:
        self.refreshed.append((harness_id, adapter, observed_version))
        return HarnessHealthResult(harness_id=harness_id, available=True, cause=None, degradations=())


@pytest.mark.unit
def test_capability_snapshot_refreshes_each_harness_health_with_its_own_adapter_and_version() -> None:
    claude, opencode = _harness(), _harness()
    claude.harness_version, opencode.harness_version = "1.0", "2.0"
    registry = HarnessRegistry(
        {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=claude), OPENCODE_HARNESS_ID: HarnessBinding(adapter=opencode)}
    )
    health = _RecordingHealth()

    capability_snapshot(registry, health=cast(HarnessHealthCache, health))

    assert health.refreshed == [(CLAUDE_CODE_HARNESS_ID, claude, "1.0"), (OPENCODE_HARNESS_ID, opencode, "2.0")]


@pytest.mark.unit
def test_registry_distinguishes_a_known_unavailable_capability() -> None:
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=_harness())})

    with pytest.raises(UnavailableHarnessError) as raised:
        registry.transcript_source(CLAUDE_CODE_HARNESS_ID)
    assert raised.value.capability == "transcript source"


@pytest.mark.unit
def test_export_app_has_an_empty_hermetic_harness_registry() -> None:
    app = create_app_for_export()

    assert app.state.harnesses.known_harnesses == ()


@pytest.mark.unit
def test_production_registry_shares_one_process_launcher_across_both_bindings(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    """Both bindings inherit ONE runner-side launcher — an identity check, not equality."""
    registry = build_production_harness_registry(
        RunnerConfig(root=tmp_path, db_url="sqlite://"), process=LinuxProcessProbe(), executor=spawn_executor
    )

    claude_launcher = vars(registry.lifecycle(CLAUDE_CODE_HARNESS_ID))["_launcher"]
    opencode_launcher = vars(registry.lifecycle(OPENCODE_HARNESS_ID))["_launcher"]
    assert claude_launcher is opencode_launcher


@pytest.mark.unit
def test_production_registry_wires_a_real_opencode_transcript_source(tmp_path: Path, spawn_executor: Executor) -> None:
    """OpenCode's binding now names a real transcript source on both the adapter and
    the binding — no longer the ``UnavailableHarnessError`` an unset binding used to raise."""
    registry = build_production_harness_registry(
        RunnerConfig(root=tmp_path, db_url="sqlite://"), process=LinuxProcessProbe(), executor=spawn_executor
    )

    source = registry.transcript_source(OPENCODE_HARNESS_ID)
    assert isinstance(source, OpenCodeTranscriptSource)
    adapter_source = vars(registry.lifecycle(OPENCODE_HARNESS_ID))["_transcript_source"]
    assert adapter_source is source


@pytest.mark.unit
def test_production_registry_injects_a_file_price_catalog_from_worker_env_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    """The OpenCode binding's price catalog is resolved from the same
    ``[worker] env_passthrough`` the worker's own environment is built from — never a
    constant path — and it is a real file-backed catalog, not left unset."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg-cache"))
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", worker_env_passthrough=("XDG_CACHE_HOME",))

    registry = build_production_harness_registry(config, process=LinuxProcessProbe(), executor=spawn_executor)

    catalog = vars(registry.lifecycle(OPENCODE_HARNESS_ID))["_price_catalog"]
    assert isinstance(catalog, FileOpenCodePriceCatalog)
    assert vars(catalog)["_path"] == tmp_path / "xdg-cache" / "opencode" / "models.json"


@pytest.mark.unit
def test_production_registry_binds_no_price_catalog_with_an_unresolvable_cache_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, spawn_executor: Executor
) -> None:
    """Neither ``XDG_CACHE_HOME`` nor ``HOME`` reaching the worker leaves the cache root
    unresolvable, so the binding skips the catalog entirely rather than one rooted at cwd."""
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("HOME", raising=False)
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")

    registry = build_production_harness_registry(config, process=LinuxProcessProbe(), executor=spawn_executor)

    assert vars(registry.lifecycle(OPENCODE_HARNESS_ID))["_price_catalog"] is None


@pytest.mark.unit
def test_production_registry_omits_a_disabled_claude_code_and_defaults_to_opencode(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    config = with_claude_code(RunnerConfig(root=tmp_path, db_url="sqlite://"), enabled=False)
    registry = build_production_harness_registry(config, process=LinuxProcessProbe(), executor=spawn_executor)

    assert default_harness_id(registry) == OPENCODE_HARNESS_ID
    registry.lifecycle(OPENCODE_HARNESS_ID)
    # A session recorded under the disabled harness resolves through the unknown-owner path.
    with pytest.raises(UnknownHarnessError):
        registry.lifecycle(CLAUDE_CODE_HARNESS_ID)


@pytest.mark.unit
def test_production_registry_omits_a_disabled_opencode(tmp_path: Path, spawn_executor: Executor) -> None:
    registry = build_production_harness_registry(
        with_opencode(RunnerConfig(root=tmp_path, db_url="sqlite://"), enabled=False),
        process=LinuxProcessProbe(),
        executor=spawn_executor,
    )

    assert default_harness_id(registry) == CLAUDE_CODE_HARNESS_ID
    with pytest.raises(UnknownHarnessError):
        registry.lifecycle(OPENCODE_HARNESS_ID)


@pytest.mark.unit
def test_production_registry_defaults_to_claude_code_with_both_enabled(
    tmp_path: Path, spawn_executor: Executor
) -> None:
    assert default_harness_id(
        build_production_harness_registry(
            RunnerConfig(root=tmp_path, db_url="sqlite://"), process=LinuxProcessProbe(), executor=spawn_executor
        )
    ) == (CLAUDE_CODE_HARNESS_ID)


@pytest.mark.unit
def test_production_health_probes_omit_a_disabled_harness(tmp_path: Path) -> None:
    base = RunnerConfig(root=tmp_path, db_url="sqlite://")
    assert list(build_production_harness_health_probes(base, spawn_root="")) == [
        CLAUDE_CODE_HARNESS_ID,
        OPENCODE_HARNESS_ID,
    ]
    no_claude = with_claude_code(RunnerConfig(root=tmp_path, db_url="sqlite://"), enabled=False)
    assert list(build_production_harness_health_probes(no_claude, spawn_root="")) == [OPENCODE_HARNESS_ID]
    no_opencode = with_opencode(RunnerConfig(root=tmp_path, db_url="sqlite://"), enabled=False)
    assert list(build_production_harness_health_probes(no_opencode, spawn_root="")) == [CLAUDE_CODE_HARNESS_ID]


def _probe_auth_path(config: RunnerConfig) -> Path | None:
    probe = build_production_harness_health_probes(config, spawn_root="")[OPENCODE_HARNESS_ID]
    assert isinstance(probe, OpenCodeHealthProbe)
    return vars(probe)["_auth_path"]


@pytest.mark.unit
def test_production_opencode_probe_follows_the_worker_env_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A daemon ``XDG_DATA_HOME`` the worker never receives does not move the probe."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "daemon-xdg"))
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")

    assert _probe_auth_path(config) == tmp_path / "home" / ".local" / "share" / "opencode" / "auth.json"


@pytest.mark.unit
def test_production_opencode_probe_follows_a_passed_through_xdg_data_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", worker_env_passthrough=("XDG_DATA_HOME",))

    assert _probe_auth_path(config) == tmp_path / "xdg" / "opencode" / "auth.json"


@pytest.mark.unit
def test_production_opencode_probe_prefers_an_explicit_auth_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    explicit = tmp_path / "elsewhere" / "auth.json"
    config = with_opencode(RunnerConfig(root=tmp_path, db_url="sqlite://"), auth_path=str(explicit))

    assert _probe_auth_path(config) == explicit
