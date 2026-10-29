"""``tests/daemon-env.json`` is held to the code it restates: guard, strict-read, and legacy variable names, and the
refusals a daemon raises."""

from __future__ import annotations

import pytest

from blizzard.cli.config_inputs import STRICT_CONFIG_ENVIRONMENT_VARIABLE
from blizzard.foundation import crash
from blizzard.foundation.runner_token_names import HUB_TOKEN_VARIABLE_PREFIX
from blizzard.hub.cli.context import ENV_HUB_TENANT
from blizzard.hub.domain.config.legacy_keys import LEGACY_FORGE_VARIABLES
from blizzard.hub.store import tenant_guard
from blizzard.runner.config import ENV_HUB_URL
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


def test_hub_coordinates_are_unset_and_the_token_prefix_is_stripped() -> None:
    assert {ENV_HUB_URL, ENV_HUB_TENANT} <= set(DAEMON_ENV["unset"])
    assert HUB_TOKEN_VARIABLE_PREFIX.rstrip("_") in DAEMON_ENV["unsetPrefixes"]


def test_the_crash_point_is_unset_so_no_ambient_arming_reaches_a_daemon() -> None:
    assert crash.ENV_CRASH_POINT in DAEMON_ENV["unset"]
