"""Host-lifetime process probes and child launching."""

from blizzard.foundation.registry.interface import Lifetime, Registry, port
from blizzard.runner.process.child_processes import IChildProcesses
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from blizzard.runner.process.internal.subprocess_child_processes import SubprocessChildProcesses
from blizzard.runner.process.probe import IProcessProbe


def register_host_infrastructure(registry: Registry) -> None:
    probe = registry.service(LinuxProcessProbe, lifetime=Lifetime.PROCESS)
    registry.alias(port(IProcessProbe), probe)
    launch = registry.service(SubprocessChildProcesses, lifetime=Lifetime.PROCESS)
    registry.alias(port(IChildProcesses), launch)
