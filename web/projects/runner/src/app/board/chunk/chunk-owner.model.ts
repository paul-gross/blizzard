import type { PanelLease, RunnerOwner } from '../../core/panel-reads.model';

/**
 * The runner a chunk's page reads under: the runner of the chunk's freshest lease (the merged
 * leases come freshest first), else — no lease naming it, since any serving runner proxies a
 * chunk's detail from the hub — the runner the Runner filter scopes to, else the host's first
 * serving runner; `null` only while the host serves no runner.
 */
export function chunkReadOwner(
  leases: readonly PanelLease[],
  chunkId: string | null,
  serving: readonly RunnerOwner[],
  scoped: readonly RunnerOwner[],
): RunnerOwner | null {
  const leased = leases.find((lease) => lease.chunk_id === chunkId)?.owner;
  return leased ?? scoped[0] ?? serving[0] ?? null;
}
