"""Store revisions reach new harness invocations without changing retained owners."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.settings_record.vocabulary import Change
from blizzard.runner.cli.runtime import seed_runners
from blizzard.runner.composition import build_host_process
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import WorkerHandle, WorkerPreamble
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.harness_set import WorkerInvocation
from blizzard.runner.harness.opencode.section import OPENCODE_HARNESS_ID
from blizzard.runner.harness.preamble import Preamble
from blizzard.runner.harness.registry import IHarnessRegistry
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.loop_wiring import RunnerStepper
from blizzard.runner.store.internal.identity_store import RunnerIdentityStore
from tests.support.child_launch import wait_for_exit
from tests.support.config_inputs import load_host_config
from tests.support.host_files import TEST_RUNNER_ID, write_minimal_host
from tests.support.opencode_adapter import preamble as opencode_preamble
from tests.support.opencode_binary import worker_binary
from tests.support.runner_fakes import make_envelope, make_store

CAPTURE_WORKER = """#!/usr/bin/python3
import json, os, pathlib, sys, time
root = pathlib.Path.cwd()
staged = root / "capture.json.tmp"
staged.write_text(json.dumps({"binary": sys.argv[0], "argv": sys.argv[1:], "probe": os.environ.get("SESSION_EDIT_PROBE"), "path": os.environ.get("PATH"), "shell": os.environ.get("CLAUDE_CODE_SHELL")}))
staged.replace(root / "capture.json")
while not (root / "release").exists():
    time.sleep(0.01)
"""


def test_capture_worker_publishes_only_complete_json(tmp_path: Path) -> None:
    script = CAPTURE_WORKER.replace(
        'staged.replace(root / "capture.json")',
        'assert json.loads(staged.read_text())["argv"] == ["argument"]\n'
        'assert not (root / "capture.json").exists()\n'
        'staged.replace(root / "capture.json")',
    )
    assert script != CAPTURE_WORKER
    binary = tmp_path / "worker"
    binary.write_text(script)
    binary.chmod(0o755)
    (tmp_path / "release").touch()

    subprocess.run([str(binary), "argument"], cwd=tmp_path, check=True, timeout=10)

    assert json.loads((tmp_path / "capture.json").read_text())["argv"] == ["argument"]
    assert not (tmp_path / "capture.json.tmp").exists()


def test_store_edits_reach_next_launch_and_preserve_running_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    monkeypatch.setenv("SESSION_EDIT_PROBE", "passed")
    old_bin, new_bin = tmp_path / "old-bin", tmp_path / "new-bin"
    for binary in (old_bin, new_bin):
        binary.write_text(CAPTURE_WORKER)
        binary.chmod(0o755)
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"', runner=f'[runner.claude_code]\nbinary = "{old_bin}"\n')
    host = load_host_config(tmp_path)
    store = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(store.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, datetime(2026, 7, 13, tzinfo=UTC))
    )
    ready, halted = seed_runners(host, clock=SystemClock())
    assert not halted
    graph = build_host_process(host, ready)
    runner = graph.runners[0]
    reader = runner.settings_reader
    assert reader is not None
    stepper = RunnerStepper(runner.config, process_graph=runner)
    handles: list[WorkerHandle] = []
    dirs: list[Path] = []
    try:
        settings = runner.stores.settings

        def edit(values: dict[str, object]) -> None:
            settings.write(
                Change("edit", sets=values),
                if_match=settings.snapshot().revision,
                validate=reader.validate,
                door="api",
                actor="test",
            )

        edit({"models.aliases": {"tier": "old-model"}, "harness.autonomy": "dangerous"})
        before = stepper._context()  # pyright: ignore[reportPrivateUsage]
        sets = before.harness_sets
        assert sets is not None
        old = sets.current
        owner = WorkerInvocation("lease-old", 1)
        sets.retain(owner)

        def launch(registry: IHarnessRegistry, name: str) -> dict[str, Any]:
            workdir = tmp_path / name
            workdir.mkdir()
            dirs.append(workdir)
            adapter = registry.lifecycle(CLAUDE_CODE_HARNESS_ID)
            model = registry.model_resolution(CLAUDE_CODE_HARNESS_ID).resolve_model(["tier"])
            launch_settings = replace(adapter.launch_settings(), model=model)
            preamble = WorkerPreamble([AcquiredEnvironment(name, str(workdir))], name, "http://localhost")
            envelope = make_envelope(name, "build", node_id="nd_build", choices=[("pass", "ok")])
            handle = adapter.spawn(
                envelope, preamble, session_hint=name, session_settings=launch_settings
            ).await_identity(0)
            handle.confirm_durable()
            handles.append(handle)
            deadline = time.monotonic() + 10
            while not (workdir / "capture.json").exists():
                assert time.monotonic() < deadline
                time.sleep(0.01)
            return json.loads((workdir / "capture.json").read_text())

        first = launch(old.registry, "first")
        edit(
            {
                "models.aliases": {"tier": "new-model"},
                "harness.autonomy": "normal",
                "worker.env_passthrough": ["SESSION_EDIT_PROBE"],
                "worker.path_prepend": [str(tmp_path)],
                "worker.shell": "/bin/sh",
                "claude_code.binary": str(new_bin),
            }
        )
        after = stepper._context()  # pyright: ignore[reportPrivateUsage]
        assert after.harness_sets is sets
        assert sets.find(owner) is old
        assert sets.current is not old
        second = launch(sets.current.registry, "second")
        assert first["binary"] == str(old_bin) and second["binary"] == str(new_bin)
        assert first["argv"][first["argv"].index("--model") + 1] == "old-model"
        assert second["argv"][second["argv"].index("--model") + 1] == "new-model"
        assert "bypassPermissions" in first["argv"] and "bypassPermissions" not in second["argv"]
        assert first["probe"] is None and second["probe"] == "passed"
        assert second["path"].split(":")[0] == str(tmp_path)
        assert first["shell"] != second["shell"] == "/bin/sh"
        assert json.loads((dirs[0] / "capture.json").read_text()) == first
    finally:
        for workdir in dirs:
            (workdir / "release").touch()
        for handle in handles:
            wait_for_exit(handle.pid)
        stepper.close()
        graph.close()


def test_opencode_store_edits_reach_new_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    old_dir, new_dir = tmp_path / "old", tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()
    old_capture, new_capture = old_dir / "capture", new_dir / "capture"
    old_bin = worker_binary(old_dir, capture_path=old_capture)
    new_bin = worker_binary(new_dir, capture_path=new_capture)
    old_config, new_config = old_dir / "config.json", new_dir / "config.json"
    old_config.write_text('{"permission":{},"plugin":[]}')
    new_config.write_text('{"permission":{"question":"deny"},"plugin":[]}')
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"')
    host = load_host_config(tmp_path)
    store = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(store.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, datetime(2026, 7, 13, tzinfo=UTC))
    )
    ready, halted = seed_runners(host, clock=SystemClock())
    assert not halted
    graph = build_host_process(host, ready)
    runner = graph.runners[0]
    reader = runner.settings_reader
    assert reader is not None
    stepper = RunnerStepper(runner.config, process_graph=runner)
    settings = runner.stores.settings

    def launch(binary: str, config: Path, workdir: Path) -> dict[str, Any]:
        settings.write(
            Change("edit", sets={"opencode.binary": str(binary), "opencode.worker_config_path": str(config)}),
            if_match=settings.snapshot().revision,
            validate=reader.validate,
            door="api",
            actor="test",
        )
        context = stepper._context()  # pyright: ignore[reportPrivateUsage]
        adapter = context.harnesses.lifecycle(OPENCODE_HARNESS_ID)
        envelope = make_envelope("chunk", "build", node_id="nd_build", choices=[("pass", "ok")])
        pending = adapter.spawn(
            envelope,
            opencode_preamble(str(workdir), stdout_path=str(workdir / "stdout")),
            None,
            session_settings=adapter.launch_settings(),
        )
        pending.confirm_durable()
        handle = pending.await_identity(5)
        wait_for_exit(handle.pid)
        return json.loads((workdir / "capture").read_text())

    try:
        first = launch(old_bin, old_config, old_dir)
        second = launch(new_bin, new_config, new_dir)
        assert first["config"] == str(old_config)
        assert second["config"] == str(new_config)
        assert first["content"] == old_config.read_text()
        assert second["content"] == new_config.read_text()
    finally:
        stepper.close()
        graph.close()


def test_prompt_and_transcript_root_edits_reach_session_consumers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    old_root, new_root = tmp_path / "old-projects", tmp_path / "new-projects"
    for root, text in ((old_root, "old transcript"), (new_root, "new transcript")):
        (root / "project").mkdir(parents=True)
        (root / "project" / "session.jsonl").write_text(text + "\n")
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"')
    host = load_host_config(tmp_path)
    store = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(store.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, datetime(2026, 7, 13, tzinfo=UTC))
    )
    ready, halted = seed_runners(host, clock=SystemClock())
    assert not halted
    graph = build_host_process(host, ready)
    runner = graph.runners[0]
    reader = runner.settings_reader
    assert reader is not None
    stepper = RunnerStepper(runner.config, process_graph=runner)
    settings = runner.stores.settings

    def edit(values: dict[str, object]) -> None:
        settings.write(
            Change("edit", sets=values),
            if_match=settings.snapshot().revision,
            validate=reader.validate,
            door="api",
            actor="test",
        )

    def preamble(prompt: str) -> str:
        return Preamble.of(
            runner_prompt=prompt,
            workspace_prompt="",
            environments=[],
            lease_id="lease",
            runner_id=TEST_RUNNER_ID,
            chunk_id="chunk",
            node="build",
        ).text

    try:
        edit({"runner_prompt": "first standing prompt", "transcripts_root": str(old_root)})
        before = stepper._context()  # pyright: ignore[reportPrivateUsage]
        source = before.harnesses.transcript_source(CLAUDE_CODE_HARNESS_ID)
        assert "first standing prompt" in preamble(before.config.runner_prompt)
        assert source.read_raw_lines("session", spawn_cwd=None) == ["old transcript"]
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("file standing prompt")
        edit(
            {
                "runner_prompt": "ignored inline",
                "runner_prompt_file": str(prompt_file),
                "transcripts_root": str(new_root),
            }
        )
        after = stepper._context()  # pyright: ignore[reportPrivateUsage]
        assert "file standing prompt" in preamble(after.config.runner_prompt)
        assert "ignored inline" not in preamble(after.config.runner_prompt)
        assert after.harnesses.transcript_source(CLAUDE_CODE_HARNESS_ID).read_raw_lines("session", spawn_cwd=None) == [
            "new transcript"
        ]
        assert source.read_raw_lines("session", spawn_cwd=None) == ["old transcript"]
    finally:
        stepper.close()
        graph.close()


def test_live_bundle_enabled_and_auth_changes_reach_consumers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from blizzard.runner.harness.wiring import first_bindings
    from tests.support.harness_bundle import bundle

    monkeypatch.setenv("BZ_HUB_TOKEN_RUNNER_LOCAL", "test-token")
    old_base, new_base = tmp_path / "old", tmp_path / "new"
    old_base.mkdir()
    new_base.mkdir()
    old_bundle, new_bundle = bundle(old_base), bundle(new_base)
    (old_bundle / "opencode" / "prompts" / "p.txt").write_text("old bundle")
    (new_bundle / "opencode" / "prompts" / "p.txt").write_text("new bundle")
    (tmp_path / "opencode-worker-config.json").write_text('{"permission":{},"plugin":[]}')
    capture = tmp_path / "capture"
    binary = worker_binary(tmp_path, capture_path=capture)
    old_auth, new_auth = tmp_path / "old-auth", tmp_path / "new-auth"
    old_auth.write_text('{"provider":{}}')
    new_auth.touch()
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"')
    host = load_host_config(tmp_path)
    store = make_store(url, TEST_RUNNER_ID)
    RunnerIdentityStore(store.connections).record_runner_identity(
        RunnerIdentity(TEST_RUNNER_ID, host.runners[0].name, datetime(2026, 7, 13, tzinfo=UTC))
    )
    ready, halted = seed_runners(host, clock=SystemClock())
    assert not halted
    graph = build_host_process(host, ready, selections={TEST_RUNNER_ID: first_bindings()}, publish_bundles=True)
    runner = graph.runners[0]
    reader = runner.settings_reader
    assert reader is not None
    stepper = RunnerStepper(runner.config, process_graph=runner)
    settings = runner.stores.settings

    def edit(values: dict[str, object]) -> None:
        settings.write(
            Change("edit", sets=values),
            if_match=settings.snapshot().revision,
            validate=reader.validate,
            door="api",
            actor="test",
        )

    def launch(registry: IHarnessRegistry, workdir: Path) -> dict[str, Any]:
        adapter = registry.lifecycle(OPENCODE_HARNESS_ID)
        pending = adapter.spawn(
            make_envelope("chunk", "build", node_id="nd_build", choices=[("pass", "ok")]),
            opencode_preamble(str(workdir), stdout_path=str(workdir / "stdout")),
            None,
            session_settings=adapter.launch_settings(),
        )
        pending.confirm_durable()
        handle = pending.await_identity(5)
        wait_for_exit(handle.pid)
        return json.loads(capture.read_text().splitlines()[-1])

    try:
        edit(
            {
                "opencode.enabled": True,
                "opencode.binary": binary,
                "opencode.auth_path": str(old_auth),
                "harness.config_dir": str(old_bundle),
            }
        )
        before = stepper._context()  # pyright: ignore[reportPrivateUsage]
        sets = before.harness_sets
        assert sets is not None
        old = sets.retain(WorkerInvocation("old", 1))
        assert old.probes[OPENCODE_HARNESS_ID].probe_authentication()
        first = launch(old.registry, old_base)
        assert Path(first["directory"]).joinpath("prompts/p.txt").read_text() == "old bundle"

        edit({"opencode.auth_path": str(new_auth), "harness.config_dir": str(new_bundle)})
        stepper._context()  # pyright: ignore[reportPrivateUsage]
        new = sets.current
        assert new is not old
        assert not new.probes[OPENCODE_HARNESS_ID].probe_authentication()
        assert old.probes[OPENCODE_HARNESS_ID].probe_authentication()
        second = launch(new.registry, new_base)
        assert Path(second["directory"]).joinpath("prompts/p.txt").read_text() == "new bundle"
        assert Path(first["directory"]).joinpath("prompts/p.txt").read_text() == "old bundle"
        edit({"opencode.enabled": False})
        stepper._context()  # pyright: ignore[reportPrivateUsage]
        assert sets.find(WorkerInvocation("old", 1)) is old
        assert OPENCODE_HARNESS_ID not in sets.current.probes
        retained = launch(old.registry, old_base)
        assert retained["directory"] == first["directory"]
    finally:
        stepper.close()
        graph.close()
