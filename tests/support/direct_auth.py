"""Explicit auth collaborators for component tests calling admission directly."""

from fastapi import Request

from blizzard.hub.api.auth import MachineCredentials
from blizzard.hub.api.auth_session import AdmittedTenant, tenant_for_request
from blizzard.hub.auth.memberships import IReadMembershipRepository
from blizzard.hub.auth.service import AuthService
from blizzard.hub.auth.sessions import IReadSessionRepository
from blizzard.hub.config import HubConfig
from blizzard.hub.open_tenants import OpenTenants
from blizzard.hub.process_root import HubProcess


def machine_for(process: HubProcess) -> MachineCredentials:
    return process.resolver.get(MachineCredentials)


def admission_for(request: Request, process: HubProcess) -> AdmittedTenant:
    resolver = process.resolver
    return tenant_for_request(
        request,
        resolver.get(OpenTenants),
        resolver.get(IReadMembershipRepository),
        resolver.get(IReadSessionRepository),
        resolver.get(AuthService),
        machine_for(process),
        resolver.get(HubConfig),
    )
