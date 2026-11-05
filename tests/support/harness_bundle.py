"""The operator harness-config bundle: config key, loader validation, atomic publish, startup
wiring on ``host``/``tick``, the ``harness status`` verb, and the no-bundle identity."""

from __future__ import annotations

import json
from pathlib import Path

from blizzard.runner.cli import runner as runner_group
from blizzard.runner.host_layout import HOST_CONFIG_FILENAME
from tests.support.runner_init_fakes import FakeInitHub, init_runner

SECRET = "sentinel-secret-value"


def bundle(tmp_path: Path) -> Path:
    root = tmp_path / "bundle"
    (root / "claude-code").mkdir(parents=True)
    (root / "claude-code" / "settings.json").write_text(json.dumps({"note": SECRET}))
    (root / "opencode" / "prompts").mkdir(parents=True)
    (root / "opencode" / "opencode.json").write_text(json.dumps({"agent": {"x": {"prompt": "{file:./prompts/p.txt}"}}}))
    (root / "opencode" / "prompts" / "p.txt").write_text(SECRET)
    return root


def runtime(tmp_path: Path, config_dir: Path | str | None = None, hub: FakeInitHub | None = None) -> Path:
    runtime = tmp_path / "runner"
    assert init_runner(hub).invoke(runner_group, ["init", str(runtime)]).exit_code == 0
    if config_dir is not None:
        path = runtime / HOST_CONFIG_FILENAME
        path.write_text(
            path.read_text().replace("[runner.harness]\n", f'[runner.harness]\nconfig_dir = "{config_dir}"\n', 1)
        )
    return runtime
