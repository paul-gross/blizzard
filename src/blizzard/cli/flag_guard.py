"""Hold a client verb, or one of its options, behind a flag (``bzh:flag-hides-behavior``).

The verb stays in ``--help``, marked while the packaged registry's default for the flag is off, so the help text, and
the recorded CLI contract, depend on the release alone. When the verb runs — or the option is given — it asks the
daemon it addresses whether the flag is on, and exits 1 with the one refusal message when it is off or the daemon
does not answer it. A hub verb and a runner verb each bind this to their daemon's flags answer."""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from typing import Any

import click
from click.core import ParameterSource

from blizzard.foundation.flags import FlagRegistry, FlagSide, side_answers
from blizzard.wire.flags import FlagOff

#: Reads the flags answer of the daemon the running verb addresses.
type AskFlags = Callable[[click.Context], Mapping[str, bool]]

_GIVEN = (ParameterSource.COMMANDLINE, ParameterSource.ENVIRONMENT)
_MARKER = "Not yet on: this works only once the flag {flag} is on."


def flag_refusal(flag: str) -> click.ClickException:
    """The refusal of a verb or option whose flag is off — the one message text the daemons' ``404`` also carries."""
    return click.ClickException(FlagOff.of(flag).message)


def check_declared(flag: str, side: FlagSide, registry: FlagRegistry) -> None:
    """Refuse a declaration naming a flag the registry lacks, or one the addressed ``side`` does not answer."""
    scope = registry.scope_of(flag)
    if scope is None:
        raise ValueError(f"flag {flag!r} guards a verb but is not declared in the flag registry")
    if not side_answers(side, scope):
        raise ValueError(f"flag {flag!r} has scope {scope!r}, which the {side} a verb addresses does not answer")


def _marked(text: str | None, flag: str, registry: FlagRegistry) -> str | None:
    """``text`` with the marker appended while the flag's packaged default is off."""
    live = registry.live.get(flag)
    if live is None or live.default:
        return text
    marker = _MARKER.format(flag=flag)
    return f"{text}\n\n{marker}" if text else marker


def _gate(flag: str, ask: AskFlags, option: str | None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorate(callback: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(callback)
        def gated(*args: Any, **kwargs: Any) -> Any:
            ctx = click.get_current_context()
            asked = option is None or ctx.get_parameter_source(option) in _GIVEN
            if asked and not ask(ctx).get(flag, False):
                raise flag_refusal(flag)
            return callback(*args, **kwargs)

        return gated

    return decorate


def flag_command(
    flag: str, side: FlagSide, ask: AskFlags, *, registry: FlagRegistry
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Hold a verb's callback behind ``flag``; apply it beneath ``@click.command`` so its docstring takes the marker."""
    check_declared(flag, side, registry)

    def decorate(callback: Callable[..., Any]) -> Callable[..., Any]:
        gated = _gate(flag, ask, None)(callback)
        gated.__doc__ = _marked(callback.__doc__ and callback.__doc__.strip(), flag, registry)
        return gated

    return decorate


def flag_option(
    flag: str, side: FlagSide, ask: AskFlags, *decls: str, registry: FlagRegistry, **attrs: Any
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """A ``click.option`` held behind ``flag``: present and marked in ``--help``, refused once given while the flag is
    off. ``decls`` and ``attrs`` are ``click.option``'s."""
    check_declared(flag, side, registry)
    attrs["help"] = _marked(attrs.get("help"), flag, registry)
    name = click.Option(list(decls), **attrs).name
    assert name is not None

    def decorate(callback: Callable[..., Any]) -> Callable[..., Any]:
        return click.option(*decls, **attrs)(_gate(flag, ask, name)(callback))

    return decorate
