"""What each hub client verb loads, in a fresh interpreter: no daemon or store stack and no other group's module."""

from __future__ import annotations

import pytest

from blizzard.hub.cli import _COMMANDS  # pyright: ignore[reportPrivateUsage]  # the sampled set is checked against it
from tests.support.loaded_modules import DAEMON_STACKS, loaded_after_running, within

#: Nothing listens here, so each verb fails at its first request, having loaded everything it needs.
_NOWHERE = ["--hub-url", "http://127.0.0.1:1"]

#: One verb per lazy client group. ``login`` and ``logout`` load the module ``rotate-signing-key`` does.
_VERBS = {
    "admin": "admin list",
    "analytics": "analytics events",
    "backlog": "backlog get",
    "cadence": "cadence list",
    "chunk": "chunk list",
    "config": "config changes",
    "credential": "credential list",
    "decision": "decision list",
    "egress": "egress status",
    "events": "events",
    "finding": "finding list",
    "flow": "flow list",
    "forge": "forge list",
    "garden-proposal": "garden-proposal list",
    "graph": "graph list",
    "invite": "invite list",
    "item": "item delete source:1",
    "member": "member list",
    "project": "project list",
    "question": "question list",
    "queue": "queue get",
    "repo": "repo list",
    "rotate-signing-key": "rotate-signing-key",
    "routine": "routine list",
    "run": "run list",
    "runner": "runner list",
    "runner-host": "runner-host list",
    "scope": "scope list",
    "secret": "secret list",
    "settings": "settings get",
    "source": "source list",
    "status": "status",
    "tenant": "tenant list",
    "traces": "traces status",
    "urgency": "urgency list",
}

#: Shrink-only. ``graph`` inlines a local graph file into the domain's graph model, and ``credential``'s help names the
#: domain's binding names; neither is a daemon or store class.
_ALLOWED_STACKS = {
    "credential": ("blizzard.hub.domain",),
    "graph": ("blizzard.hub.domain",),
}

#: Shrink-only. A group that renders another group's rows through that group's own view.
_ALLOWED_SIBLINGS = {
    "admin": {"blizzard.hub.cli.config"},  # ``admin settings apply`` prints ``config``'s apply listing.
    "chunk": {"blizzard.hub.cli.flow"},  # chunk rows label their flow and place as ``flow`` does.
    "settings": {"blizzard.hub.cli.config"},  # ``settings changes`` prints ``config``'s change listing.
}

#: What the local verbs sharing these groups' modules build, over and above the daemon stacks.
_LOCAL_PROVIDERS = {
    "config": ("blizzard.hub.config", "blizzard.hub.tenancy"),
    "secret": ("blizzard.foundation.secrets", "blizzard.hub.config"),
}

#: The lazy entries that are local verbs, or share another entry's module.
_NOT_SAMPLED = {"init", "migrate", "host", "login", "logout"}


def test_every_lazy_client_group_is_sampled() -> None:
    assert set(_VERBS) == set(_COMMANDS) - _NOT_SAMPLED


@pytest.mark.parametrize("group", sorted(_VERBS))
def test_a_client_verb_loads_no_daemon_stack_and_no_sibling_group(group: str) -> None:
    loaded = loaded_after_running(["hub", *_VERBS[group].split(), *_NOWHERE])

    own = _COMMANDS[group].partition(":")[0]
    siblings = {target.partition(":")[0] for target in _COMMANDS.values()} - {own}
    allowed = _ALLOWED_STACKS.get(group, ())
    assert own in loaded
    assert within(loaded, tuple(stack for stack in DAEMON_STACKS if stack not in allowed)) == []
    assert siblings & loaded == _ALLOWED_SIBLINGS.get(group, set())
    assert sorted(m for m in loaded if m.startswith("blizzard.") and m.endswith(".wiring")) == [
        "blizzard.cli.wiring",
        "blizzard.hub.cli.wiring",
    ]
    assert within(loaded, _LOCAL_PROVIDERS.get(group, ())) == []
