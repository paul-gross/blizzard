"""The shared command root's closing contract: what finalizers are handed, and which failure the operator sees."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import AbstractContextManager

import click
import pytest
from click.testing import CliRunner

from blizzard.cli.command_root import Area, command_scope
from blizzard.foundation.registry.interface import Lifetime, Registry
from blizzard.foundation.registry.scopes import ScopeValues


class _Held:
    pass


class _Unbuildable:
    pass


def _holding(sent: list[BaseException | None]) -> Area:
    """An area owning one ``_Held``, recording what its finalizer is handed."""

    def open_held() -> Generator[_Held, BaseException | None]:
        sent.append((yield _Held()))

    def register(registry: Registry) -> None:
        registry.resource(open_held, lifetime=Lifetime.COMMAND)

    return register


def _unbuildable(failure: Exception) -> Area:
    def build() -> _Unbuildable:
        raise failure

    def register(registry: Registry) -> None:
        registry.factory(build, lifetime=Lifetime.COMMAND)

    return register


def _failing_cleanup(registry: Registry) -> None:
    def open_held() -> Generator[_Held]:
        yield _Held()
        raise RuntimeError("could not flush")

    registry.resource(open_held, lifetime=Lifetime.COMMAND)


def _scope(*areas: Area, **collaborators: type[object]) -> AbstractContextManager[dict[str, object]]:
    return command_scope(areas, owner="test", values=ScopeValues(), collaborators=collaborators)


def test_a_finalizer_is_handed_nothing_when_the_command_succeeds() -> None:
    sent: list[BaseException | None] = []

    with _scope(_holding(sent), held=_Held) as resolved:
        assert isinstance(resolved["held"], _Held)
        assert sent == []

    assert sent == [None]


def test_a_finalizer_is_handed_the_failure_the_command_ends_in() -> None:
    sent: list[BaseException | None] = []
    failure = click.ClickException("refused")

    with pytest.raises(click.ClickException), _scope(_holding(sent), held=_Held):
        raise failure

    assert sent == [failure]


def test_a_resolution_failure_closes_what_was_already_built() -> None:
    sent: list[BaseException | None] = []
    failure = RuntimeError("no such thing")

    with pytest.raises(RuntimeError), _scope(_holding(sent), _unbuildable(failure), held=_Held, other=_Unbuildable):
        pytest.fail("the block never runs")

    assert sent == [failure]


def test_a_failing_finalizer_leaves_the_commands_message_and_exit_code() -> None:
    @click.command()
    def refused() -> None:
        with _scope(_failing_cleanup, held=_Held):
            raise click.ClickException("refused")

    result = CliRunner().invoke(refused)

    assert result.exit_code == 1
    assert result.stderr == "warning: cleanup failed: RuntimeError: could not flush\nError: refused\n"


def test_a_failing_finalizer_fails_a_command_that_otherwise_succeeded() -> None:
    with pytest.raises(ExceptionGroup) as raised, _scope(_failing_cleanup, held=_Held):
        pass

    assert [str(failure) for failure in raised.value.exceptions] == ["could not flush"]
