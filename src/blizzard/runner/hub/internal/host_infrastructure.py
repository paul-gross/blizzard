"""The host hub transport and registration factories."""

from collections.abc import Iterator
from dataclasses import dataclass

import httpx2

from blizzard.foundation.clock import IClock
from blizzard.foundation.platform_tracing.handle import IPlatformTracing
from blizzard.runner.hub.host_identity import HostIdentityHolder, IWriteHostIdentityRepository
from blizzard.runner.hub.host_options import HostHubOptions
from blizzard.runner.hub.host_registration import HostRegistration, HostRegistrationHolder
from blizzard.runner.hub.internal.http_hub import HttpHostHubClient


@dataclass(frozen=True)
class HostHubTransport:
    client: httpx2.Client


def open_host_hub_transport(options: HostHubOptions, tracing: IPlatformTracing) -> Iterator[HostHubTransport]:
    client = httpx2.Client(base_url=options.url, timeout=10.0)
    try:
        tracing.instrument_client(client)
    except BaseException as failure:
        try:
            client.close()
        except BaseException as cleanup:
            failure.add_note(f"closing the hub client after instrumentation also failed: {cleanup!r}")
        raise
    try:
        yield HostHubTransport(client)
    finally:
        client.close()


def host_hub_client(transport: HostHubTransport, options: HostHubOptions) -> HttpHostHubClient:
    return HttpHostHubClient(transport.client, token=options.token)


def host_registration_holder(options: HostHubOptions) -> HostRegistrationHolder:
    return HostRegistrationHolder.for_host(registered=bool(options.token))


def host_registration(
    client: HttpHostHubClient,
    options: HostHubOptions,
    identities: IWriteHostIdentityRepository,
    identity: HostIdentityHolder,
    status: HostRegistrationHolder,
    clock: IClock,
) -> HostRegistration:
    return HostRegistration(client, options.report, identities, identity, status, options.runners, clock)
