"""Scaffold harness files after a fixture profile is applied, without touching the host store or enrolling it."""

import json
from contextlib import suppress
from pathlib import Path
from sys import argv

from blizzard.cli.config_inputs import packaged_flag_registry, strict_config
from blizzard.foundation.clock import SystemMonotonicClock
from blizzard.runner.cli.runtime import select_runner_bindings
from blizzard.runner.harness.opencode.declaration import OpenCodeDeclaration
from blizzard.runner.harness.opencode.plugin import (
    PLUGIN_DIRNAME,
    PLUGIN_FILENAME,
    plugin_reference,
    render_plugin_source,
)
from blizzard.runner.harness.opencode.section import OpenCodeSection
from blizzard.runner.harness.opencode.worker_config import render_worker_config
from blizzard.runner.harness.wiring import declared
from blizzard.runner.host_config import HostConfig
from blizzard.runner.process.internal.subprocess_child_processes import SubprocessChildProcesses


def write_missing(root: Path, path: Path, content: str) -> None:
    if not path.resolve().is_relative_to(root):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with suppress(FileExistsError), path.open("x", encoding="utf-8") as output:
        output.write(content)


def main() -> None:
    root = Path(argv[1]).resolve()
    config = HostConfig.load(root, flag_registry=packaged_flag_registry(), strict=strict_config())
    for entry in config.runners:
        if entry.own is not None:
            for declaration, section in declared(
                entry.own.harness_sections,
                select_runner_bindings(entry.own, SubprocessChildProcesses(SystemMonotonicClock())),
            ):
                if (
                    not isinstance(declaration, OpenCodeDeclaration)
                    or not isinstance(section, OpenCodeSection)
                    or not section.enabled
                ):
                    continue
                target = section.worker_config_at(root)
                plugin = root / PLUGIN_DIRNAME / PLUGIN_FILENAME
                if not target.resolve().is_relative_to(root):
                    continue
                write_missing(root, plugin, render_plugin_source())
                document = render_worker_config(shell=entry.own.worker_shell, plugins=(plugin_reference(plugin),))
                write_missing(root, target, json.dumps(document, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
