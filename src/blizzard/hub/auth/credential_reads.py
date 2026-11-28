"""The pre-admission reads that name a machine credential's tenant."""

from typing import Protocol

from blizzard.hub.tenancy import TenantId


class RunnerCredentialTarget(Protocol):
    """A credential's runner and its tenant."""

    @property
    def tenant(self) -> TenantId: ...

    @property
    def runner_id(self) -> str: ...


class HostCredentialTarget(Protocol):
    """A credential's host and its tenant."""

    @property
    def tenant(self) -> TenantId: ...

    @property
    def host_id(self) -> str: ...


class MachineCredentialReads(Protocol):
    """Resolve a machine credential or hub-minted identity before a tenant is known."""

    def runner_for_token_hash(self, token_hash: str) -> RunnerCredentialTarget | None: ...

    def revoked_token_runner(self, token_hash: str) -> RunnerCredentialTarget | None: ...

    def runner_by_id(self, runner_id: str) -> RunnerCredentialTarget | None: ...

    def host_for_token_hash(self, token_hash: str) -> HostCredentialTarget | None: ...

    def revoked_token_host(self, token_hash: str) -> HostCredentialTarget | None: ...

    def host_by_id(self, host_id: str) -> HostCredentialTarget | None: ...
