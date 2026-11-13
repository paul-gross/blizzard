from contextlib import ExitStack
from types import SimpleNamespace
from typing import cast

from tests.harness.old_installation import register_shutdown
from tests.rehearsal.test_upgrade_rehearsal import Rehearsal


def test_runner_stops_before_hub() -> None:
    stopped: list[str] = []
    leg = SimpleNamespace(
        hub=SimpleNamespace(stop=lambda: stopped.append("hub")),
        work=SimpleNamespace(stop=lambda: stopped.append("runner")),
    )
    with ExitStack() as stack:
        register_shutdown(stack, cast(Rehearsal, leg))
    assert stopped == ["runner", "hub"]
