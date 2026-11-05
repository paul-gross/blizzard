"""The hub removes the retired ``data/hub_workdirs`` directory on boot (component tier).

Proven on ``build_hosted_app`` itself — ``tests/support/scaffolding.py::build_hub`` bypasses it —
the ``tests/component/hub/test_secret_key_coverage.py`` shape."""

from __future__ import annotations

from pathlib import Path

from blizzard.hub.config import HubConfig
from tests.support.config_inputs import build_hosted_hub_app, init_hub


def _build(config: HubConfig) -> None:
    build_hosted_hub_app(config).state.engine.dispose()


def _workdirs(config: HubConfig) -> Path:
    return config.data_dir / "hub_workdirs"


def test_boot_removes_a_populated_workdirs_directory(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")
    clone = _workdirs(config) / "chunk-1" / "repo"
    clone.mkdir(parents=True)
    (clone / "README.md").write_text("scratch\n")

    _build(config)

    assert not _workdirs(config).exists()


def test_a_second_boot_without_the_directory_succeeds(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")
    _workdirs(config).mkdir(parents=True)
    _build(config)

    _build(config)

    assert not _workdirs(config).exists()


def test_boot_unlinks_a_symlinked_workdirs_without_following_it(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep.txt").write_text("kept\n")
    _workdirs(config).symlink_to(target, target_is_directory=True)

    _build(config)

    assert not _workdirs(config).is_symlink()
    assert (target / "keep.txt").read_text() == "kept\n"
