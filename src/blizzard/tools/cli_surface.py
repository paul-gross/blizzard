"""Export the live ``blizzard hub`` / ``blizzard runner`` command trees as JSON.

The single source of ``contracts/cli/``'s golden corpus (blizzard#cli-by-concept Phase 1):
each root group is walked via plain ``click`` introspection — no invocation, no I/O — and
dumped as deterministic, sorted JSON, so a drift check over the committed output is stable
(the same shape ``blizzard.tools.openapi`` uses for the OpenAPI specs)."""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import click

from blizzard.hub.cli import hub
from blizzard.runner.cli import runner

# The two root groups the CLI decomposition plan scopes to (issue #cli-by-concept);
# `blizzard dev` stays out of scope.
ROOTS: tuple[tuple[str, click.Group], ...] = (
    ("hub", hub),
    ("runner", runner),
)

# Long enough that no real command's summary is ever ellipsized — the point is the
# derived short-help text itself, not a terminal-width-dependent truncation of it.
_SHORT_HELP_LIMIT = 10_000


def _param_shape(param: click.Parameter) -> dict[str, Any]:
    """One parameter's spelling, kind, type, and required/hidden flags."""
    return {
        "name": param.name,
        "kind": "argument" if isinstance(param, click.Argument) else "option",
        "opts": list(param.opts),
        "secondary_opts": list(param.secondary_opts),
        "type": param.type.name,
        "required": bool(param.required),
        "hidden": bool(getattr(param, "hidden", False)),
    }


def _node_shape(command: click.Command, path: str) -> dict[str, Any]:
    """One command node: its full path, help text, short help, and params in
    declaration order — recursing into every subgroup's own commands."""
    node: dict[str, Any] = {
        "path": path,
        # `command.help` is the raw docstring — whether its continuation lines carry
        # source indentation depends on the interpreter (3.13+ dedents literals at
        # compile time, earlier ones don't), so `cleandoc` it the same way click's own
        # `format_help_text` does, or the snapshot would drift on Python version alone.
        "help": inspect.cleandoc(command.help) if command.help else "",
        "short_help": command.get_short_help_str(limit=_SHORT_HELP_LIMIT),
        "params": [_param_shape(p) for p in command.params],
    }
    if isinstance(command, click.Group):
        node["commands"] = {
            name: _node_shape(child, f"{path} {name}") for name, child in sorted(command.commands.items())
        }
    return node


def build(name: str, root: click.Group) -> dict[str, Any]:
    """The full command tree under one root group, keyed at its own root path."""
    return _node_shape(root, name)


def snapshot() -> dict[str, dict[str, Any]]:
    """Both roots' command trees, keyed by root name (``hub``, ``runner``)."""
    return {name: build(name, root) for name, root in ROOTS}


def export(out_dir: Path) -> list[Path]:
    """Write each root's tree into ``out_dir`` as ``<root>.json``; return the written paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, root in ROOTS:
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(build(name, root), indent=2, sort_keys=True) + "\n")
        written.append(path)
    return written


@click.command()
@click.option(
    "--out-dir",
    default="contracts/cli",
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to write the hub/runner CLI surface JSON snapshots into.",
)
def main(out_dir: Path) -> None:
    """Dump the hub and runner CLI command trees for the surface-contract guard test."""
    for path in export(out_dir):
        click.echo(f"wrote {path}")


if __name__ == "__main__":
    main()
