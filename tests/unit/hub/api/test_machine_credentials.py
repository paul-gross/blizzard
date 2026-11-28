"""Machine credentials open only the tenant named by a credential."""

from unittest.mock import Mock

import pytest

from blizzard.foundation.registry.interface import ServiceAbsent
from blizzard.hub.api.auth import MachineCredentials, TenantHostRegistries, TenantRunnerRegistries
from blizzard.hub.tenancy import TenantClosed, TenantId, UnknownTenant


def test_registries_are_opened_for_the_named_tenant() -> None:
    runners = TenantRunnerRegistries()
    hosts = TenantHostRegistries()
    runner_registry, host_registry = Mock(), Mock()
    open_runners, open_hosts = Mock(return_value=runner_registry), Mock(return_value=host_registry)
    runners.bind(open_runners)
    hosts.bind(open_hosts)
    machine = MachineCredentials(None, runners, hosts)
    tenant = TenantId("tenant-a")

    assert machine.runner(tenant, "runner") is runner_registry.get_runner.return_value
    assert machine.host(tenant, "host") is host_registry.get_host.return_value
    open_runners.assert_called_once_with(tenant)
    open_hosts.assert_called_once_with(tenant)
    runner_registry.get_runner.assert_called_once_with("runner")
    host_registry.get_host.assert_called_once_with("host")


@pytest.mark.parametrize("exception", [UnknownTenant, TenantClosed])
def test_unavailable_tenant_returns_no_machine(exception: type[Exception]) -> None:
    tenant = TenantId("tenant-a")
    runners, hosts = TenantRunnerRegistries(), TenantHostRegistries()
    runners.bind(Mock(side_effect=exception(tenant)))
    hosts.bind(Mock(side_effect=exception(tenant)))
    machine = MachineCredentials(None, runners, hosts)

    assert machine.runner(tenant, "runner") is None
    assert machine.host(tenant, "host") is None
    with pytest.raises(ServiceAbsent):
        _ = machine.credentials
