"""``tests/daemon-env.json`` is held to the code it restates: guard, strict-read, and legacy variable names, and the
refusals a daemon raises."""

from __future__ import annotations

import pytest

from blizzard.cli.config_inputs import STRICT_CONFIG_ENVIRONMENT_VARIABLE
from blizzard.hub.domain.config.legacy_keys import LEGACY_FORGE_VARIABLES
from blizzard.hub.store import tenant_guard
from blizzard.runner.store import runner_guard
from tests.suite_plugin import DAEMON_ENV

pytestmark = pytest.mark.unit


def test_guard_variables_equal_each_guard_modules_variable() -> None:
    assert DAEMON_ENV["set"][tenant_guard.GUARD_ENVIRONMENT_VARIABLE] == "1"
    assert DAEMON_ENV["set"][runner_guard.GUARD_ENVIRONMENT_VARIABLE] == "1"


def test_strict_reads_equal_the_strict_config_variable() -> None:
    assert DAEMON_ENV["set"][STRICT_CONFIG_ENVIRONMENT_VARIABLE] == "1"


def test_unset_holds_every_legacy_forge_variable() -> None:
    assert set(LEGACY_FORGE_VARIABLES) <= set(DAEMON_ENV["unset"])


def test_refusals_name_the_two_statement_guard_exceptions() -> None:
    assert DAEMON_ENV["refusals"] == [
        tenant_guard.UnscopedTenantStatement.__name__,
        runner_guard.UnscopedRunnerStatement.__name__,
    ]
