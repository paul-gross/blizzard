"""A host directory's configuration, as a test reads and rewrites it.

The runner tiers join a runner with ``blizzard runner init`` — which scaffolds ``blizzard-host.toml`` — then
load that runner's :class:`~blizzard.runner.config.RunnerConfig`, replace the fields a scenario sets (its ports, its
hub, its gates), and write the host file back so the daemon it spawns reads them."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from blizzard.foundation.config_keys import ConfigView
from blizzard.runner.config import (
    DEFAULT_WORKSPACE_ID,
    SCAFFOLD_PROJECTS,
    Auth,
    HostWorkspace,
    ProjectBinding,
    RunnerConfig,
    SubscriptionDeclaration,
    Transcripts,
    WorkerStdout,
)
from blizzard.runner.host_config import HostConfig, RunnerEntry
from blizzard.runner.host_layout import HostLayout, write_runner_id
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.runtime import init_environment
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC
from tests.runner_fakes import FIXTURE_RUNNER_ID

#: The id of the runner a test host file declares.
TEST_RUNNER_ID = "rn_host_test"


def write_minimal_host(
    root: Path,
    *,
    top: str = "",
    workspace: str = "",
    runner: str = "",
    projects: str = "[]",
    runner_name: str = "runner-local",
) -> Path:
    """Write a small valid ``blizzard-host.toml`` into ``root``: one workspace and one runner, each with the extra
    keys the test names (``top`` before any table, ``workspace`` and ``runner`` inside their tables)."""
    root.mkdir(parents=True, exist_ok=True)
    text = (
        f"{top}\n"
        "[[workspace]]\n"
        'id = "workspace-local"\n'
        f"{workspace}\n"
        "[[runner]]\n"
        f'id = "{TEST_RUNNER_ID}"\n'
        f'name = "{runner_name}"\n'
        f"projects = {projects}\n"
        f"{runner}\n"
    )
    path = HostLayout(root).config_path
    path.write_text(text)
    return path


def init_runner_environment(root: Path | str, **kwargs: Any) -> RunnerConfig:
    """Scaffold and migrate a host directory as ``runner init`` does before it joins a hub, answering its runner.

    A runner no hub has been joined to yet is given the fixture runner's id and a placeholder token — a host starts a
    runner only when it holds both — so a scenario whose hub is not up still serves its local API."""
    config = init_environment(Path(root), **kwargs)
    runner = config.runner_config()
    if runner.runner_id is None:
        write_runner_id(config.layout, runner.name, FIXTURE_RUNNER_ID)
        HubTokenFile.of(config.root, runner.token_env).write(f"tok_{FIXTURE_RUNNER_ID}")
        runner = HostConfig.load(config.root).runner_config()
    return runner


def sampler_without_credentials(root: Path | str) -> tuple[SubscriptionDeclaration, ...]:
    """The one subscription a scaffolded runner names, its credential file a path that is never created — so the
    sampler's missing-credentials soft failure trips before any request is built."""
    return (
        SubscriptionDeclaration(
            slug="anthropic",
            name="Anthropic",
            provider=PROVIDER_ANTHROPIC,
            credentials_path=str(Path(root) / "no-such-credentials.json"),
        ),
    )


def load_runner_config(root: Path | str, *, host: str | None = None, port: int | None = None) -> RunnerConfig:
    """The sole runner of the host directory ``root``, as every downstream reader takes it."""
    return HostConfig.load(Path(root), host=host, port=port).runner_config()


def host_config_of(config: RunnerConfig) -> HostConfig:
    """The host file one runner's ``config`` stands for: its host keys at the top, its workspaces as the
    ``[[workspace]]`` entries, and the runner — with the projects it serves bound at its agent count — as the one ``[[runner]]``."""
    entry = RunnerEntry(
        name=config.name,
        id=config.runner_id,
        subscription=config.subscriptions[0].slug if config.subscriptions else None,
        bindings=_bindings_of(config),
        own=config,
    )
    return HostConfig(
        root=config.root,
        db_url=config.db_url,
        host=config.host,
        port=config.port,
        hub_url=config.hub_url,
        public_urls=config.public_urls,
        trusted_proxies=config.trusted_proxies,
        session_secret_env=config.session_secret_env,
        auth=Auth.of(
            ConfigView.unchecked(
                {
                    key: value
                    for key, value in (
                        ("superuser", config.auth_superuser),
                        ("hub_role_default", config.auth_hub_role_default),
                        ("users", dict(config.auth_users) or None),
                    )
                    if value
                }
            )
        ),
        tracing=config.tracing,
        transcripts=Transcripts.of(
            ConfigView.unchecked(
                {
                    key: value
                    for key, value in (
                        ("ship", config.transcripts_ship or None),
                        ("record_max_bytes", config.transcript_record_max_bytes),
                        ("chunk_max_bytes", config.transcript_chunk_max_bytes),
                    )
                    if value is not None
                }
            )
        ),
        worker_stdout=WorkerStdout.of(ConfigView.unchecked({"retention_days": config.worker_stdout_retention_days})),
        subscriptions=config.subscriptions,
        workspaces=config.workspaces,
        runners=(entry,),
    )


def write_host_file(config: RunnerConfig) -> None:
    """Write ``config`` as its directory's ``blizzard-host.toml``, replacing the one there."""
    HostLayout(config.root).config_path.write_text(host_config_of(config).to_toml())


def with_runner(config: RunnerConfig, **changes: object) -> RunnerConfig:
    """``config`` with ``changes`` laid over it, written to its host file; answers the changed config."""
    changed = dataclasses.replace(config, **changes)
    write_host_file(changed)
    return changed


def one_workspace(**fields: Any) -> tuple[HostWorkspace, ...]:
    """A host's ``workspaces`` holding one workspace, the default id unless ``fields`` names another."""
    return (HostWorkspace(**{"id": DEFAULT_WORKSPACE_ID, **fields}),)


def with_workspace(config: RunnerConfig, **changes: Any) -> RunnerConfig:
    """``config`` with ``changes`` laid over its first workspace."""
    first, *rest = config.workspaces
    return dataclasses.replace(config, workspaces=(dataclasses.replace(first, **changes), *rest))


def _bindings_of(config: RunnerConfig) -> tuple[ProjectBinding, ...]:
    if config.bindings:
        return config.bindings
    projects = SCAFFOLD_PROJECTS if config.projects is None else config.projects
    return tuple(ProjectBinding(project, config.home_workspace.id, 1) for project in projects)
