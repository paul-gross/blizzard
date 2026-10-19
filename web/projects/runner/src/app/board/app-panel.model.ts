import type { runnerApi } from 'fleet';

import { BOARD_LANES, type MachineChunkRow } from '../core/chunk-rows.model';
import type { MachineChunkStatus } from '../core/chunk-status';
import { deriveLeaseStatus } from '../core/lease-status';
import type { PanelLease } from '../core/panel-reads.model';

/** `rows` ranked for the list view: lane by lane in {@link BOARD_LANES} order, each lane
 * keeping the merged order (live first, then newest). */
export function rankedRows(rows: readonly MachineChunkRow[]): MachineChunkRow[] {
  const rank = (row: MachineChunkRow): number => BOARD_LANES.findIndex((lane) => lane.key === row.lane);
  return [...rows].sort((a, b) => rank(a) - rank(b));
}

function rowFor(rows: readonly MachineChunkRow[], chunkId: string | null): MachineChunkRow | undefined {
  if (chunkId === null) return undefined;
  return rows.find((chunk) => chunk.lease.chunk_id === chunkId);
}

/** The chunk's attempts (oldest → newest), or empty when nothing is selected or the chunk is unknown. */
export function chunkLeasesFor(
  rows: readonly MachineChunkRow[],
  chunkId: string | null,
): readonly PanelLease[] {
  return rowFor(rows, chunkId)?.leases ?? [];
}

/** The chunk's derived status, or `null` when nothing is selected or the chunk is unknown. */
export function chunkStatusFor(rows: readonly MachineChunkRow[], chunkId: string | null): MachineChunkStatus | null {
  return rowFor(rows, chunkId)?.status ?? null;
}

/** The chunk's open escalation on `runnerId`, the runner of its newest attempt, when one
 * exists — carries the resume command. */
export function escalationFor(
  escalations: readonly (runnerApi.EscalationView & { readonly owner: { readonly runnerId: string } })[],
  runnerId: string | null,
  chunkId: string | null,
): runnerApi.EscalationView | null {
  if (chunkId === null || runnerId === null) return null;
  return escalations.find((esc) => esc.chunk_id === chunkId && esc.owner.runnerId === runnerId) ?? null;
}

/** Whether a chunk's card or row names its derived status beside its worker — always once no
 * worker is live, else only when a human fact (a takeover, escalation or ask) outranks the
 * worker's own state, which the worker line already shows. */
export function showsChunkStatus(row: MachineChunkRow): boolean {
  return row.live === null || row.status.label !== deriveLeaseStatus(row.live).label;
}
