"""Every client verb that reads one thing is ``get``; ``show`` and ``read`` are hidden aliases (unit tier).

Walks both CLI roots in ``blizzard.tools.cli_surface.ROOTS``: no command is registered under a
reserved read-one name, every group with a ``get`` resolves each alias to that very command, and no
alias shows in a group's listing or rendered ``--help``."""

from __future__ import annotations

from collections.abc import Iterator

import click
import pytest

from blizzard.cli.get_group import GET_ALIASES, GetGroup
from blizzard.tools.cli_surface import ROOTS

pytestmark = pytest.mark.unit

_RESERVED = frozenset({*GET_ALIASES, "view", "describe", "inspect"})


def _groups() -> Iterator[tuple[str, click.Group]]:
    stack = [(name, root) for name, root in ROOTS]
    while stack:
        path, group = stack.pop()
        yield path, group
        ctx = click.Context(group)
        for name in group.list_commands(ctx):
            child = group.get_command(ctx, name)
            if isinstance(child, click.Group):
                stack.append((f"{path} {name}", child))


def test_no_command_is_registered_under_a_reserved_read_one_name() -> None:
    stray = [
        f"{path} {name}"
        for path, group in _groups()
        for name in group.list_commands(click.Context(group))
        if name in _RESERVED
    ]
    assert stray == [], f"name the read-one verb `get`: {stray}"


def test_every_group_with_a_get_resolves_the_aliases_to_it() -> None:
    for path, group in _groups():
        ctx = click.Context(group)
        if "get" not in group.list_commands(ctx):
            continue
        assert isinstance(group, GetGroup), f"{path} has a `get` but is not a GetGroup"
        for alias in GET_ALIASES:
            assert group.get_command(ctx, alias) is group.get_command(ctx, "get"), f"{path} {alias}"


def test_no_alias_appears_in_a_listing_or_help() -> None:
    for path, group in _groups():
        ctx = click.Context(group, info_name=path)
        if "get" not in group.list_commands(ctx):
            continue
        listed = group.list_commands(ctx)
        helped = group.get_help(ctx)
        for alias in GET_ALIASES:
            assert alias not in listed, path
            assert f"  {alias} " not in helped, path
