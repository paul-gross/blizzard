"""The lifecycle area's aliases onto the host store adapters it is handed."""

from __future__ import annotations

from blizzard.foundation.registry.interface import Ref, Registry, port
from blizzard.runner.lifecycle.artifacts import IReadGraphArtifactRepository, IWriteGraphArtifactRepository
from blizzard.runner.lifecycle.daemon_liveness import IReadDaemonLivenessRepository, IWriteDaemonLivenessRepository


def register_host_store_ports(
    registry: Registry,
    graph_artifacts: Ref[IWriteGraphArtifactRepository],
    daemon_liveness: Ref[IWriteDaemonLivenessRepository],
) -> None:
    """Alias this area's host ports onto the adapters handed by the process root."""
    registry.alias(port(IReadGraphArtifactRepository), graph_artifacts)
    registry.alias(port(IWriteGraphArtifactRepository), graph_artifacts)
    registry.alias(port(IReadDaemonLivenessRepository), daemon_liveness)
    registry.alias(port(IWriteDaemonLivenessRepository), daemon_liveness)
