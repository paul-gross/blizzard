"""The host_records area's aliases onto the host store adapters it is handed."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Ref, Registry, port
from blizzard.runner.host_records.ports import (
    IReadHostSubscriptionRecords,
    IReadHostWorkspaceRecords,
    IWriteHostSubscriptionRecords,
    IWriteHostWorkspaceRecords,
)


def register_host_store_ports(
    registry: Registry,
    workspaces: Ref[IWriteHostWorkspaceRecords],
    subscriptions: Ref[IWriteHostSubscriptionRecords],
) -> None:
    """Alias this area's host ports onto the adapters handed by the process root."""
    registry.alias(port(IReadHostWorkspaceRecords), workspaces)
    registry.alias(port(IWriteHostWorkspaceRecords), workspaces)
    registry.alias(port(IReadHostSubscriptionRecords), subscriptions)
    registry.alias(port(IWriteHostSubscriptionRecords), subscriptions)
