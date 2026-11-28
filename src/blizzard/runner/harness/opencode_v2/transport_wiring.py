"""Production transport for OpenCode 2 commands."""

from blizzard.runner.harness.opencode_v2.transport import IServiceTransport, UrllibServiceTransport


def service_transport() -> IServiceTransport:
    return UrllibServiceTransport()
