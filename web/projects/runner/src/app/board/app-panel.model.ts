import type { runnerApi } from 'fleet';

import { type PanelLease, type PanelProject, projectColorFor } from '../core/panel-reads.model';
import { deriveLeaseStatus } from '../core/lease-status';
import { isLiveLease } from '../core/worker-session';

import { type MachineChunkStatus, deriveMachineChunkStatus } from './chunk-list/chunk-status';

/** One row in the machine-chunks list: a chunk's newest lease plus its derived
 * machine-side status. `leases` carries *every* attempt of the chunk (oldest →
 * newest); `lease` is that same newest entry, already resolved for the row, and its
 * runner is the row's. */
export interface MachineChunkRow {
  readonly lease: PanelLease;
  readonly leases: readonly PanelLease[];
  readonly status: MachineChunkStatus;
  /** The chunk's live worker — its newest lease while that has not ended, else `null`.
   * A chunk has at most one live lease, and the merged order puts it first. */
  readonly live: PanelLease | null;
  /** The board lane the chunk sits in ({@link boardLane}). */
  readonly lane: BoardLane;
  /** The project the chunk is attributed to, by name, or `null` where none can be told. */
  readonly projectName: string | null;
  /** The project's colour the chunk is tinted with ({@link projectColorFor}), or `null`. */
  readonly projectColor: string | null;
}

/** The board's three lanes, in the order the board lays them out and the list ranks them. */
export type BoardLane = 'needs' | 'progress' | 'done';

/** The board's lanes with their headings. */
export const BOARD_LANES: readonly { readonly key: BoardLane; readonly label: string }[] = [
  { key: 'needs', label: 'Needs human' },
  { key: 'progress', label: 'In progress' },
  { key: 'done', label: 'Done' },
];

/**
 * The lane a chunk sits in, off its newest lease. An escalation or an open ask — a chunk
 * waiting on a human answer or gate — needs a human; a session ended (its worker exited, or
 * the step closed) is done; anything else — a worker running, spawning, backing off or
 * parked, or a human already in the session — is in progress.
 */
export function boardLane(lease: runnerApi.LeaseView): BoardLane {
  if (lease.display_status === 'needs-human' || lease.display_status === 'waiting-ask') return 'needs';
  if (lease.display_status === 'human-in-session') return 'progress';
  return isLiveLease(lease) ? 'progress' : 'done';
}

/** `rows` ranked for the list view: lane by lane in {@link BOARD_LANES} order, each lane
 * keeping the merged order (live first, then newest). */
export function rankedRows(rows: readonly MachineChunkRow[]): MachineChunkRow[] {
  const rank = (row: MachineChunkRow): number => BOARD_LANES.findIndex((lane) => lane.key === row.lane);
  return [...rows].sort((a, b) => rank(a) - rank(b));
}

/**
 * One row per chunk across the runners read: the chunk's newest lease (the merged
 * list orders actives first, then newest first, so the first lease
 * seen per `chunk_id` is the freshest attempt) plus every attempt of the
 * chunk and the derived status. Each row's `leases` is ordered oldest → newest,
 * so `lease` (the summary/status subject) is that list's own newest entry.
 */
export function machineChunkRows(leases: readonly PanelLease[], projects: readonly PanelProject[] = []): MachineChunkRow[] {
  // Group by chunk in merged order (newest attempt first); the Map preserves
  // first-seen insertion order, so the rows keep the newest-lease-first order.
  const grouped = new Map<string, PanelLease[]>();
  for (const lease of leases) {
    const group = grouped.get(lease.chunk_id);
    if (group) group.push(lease);
    else grouped.set(lease.chunk_id, [lease]);
  }
  const rows: MachineChunkRow[] = [];
  for (const group of grouped.values()) {
    const newest = group[0];
    const project = projects.find((p) => p.slug === newest.projectSlug) ?? null;
    rows.push({
      lease: newest,
      leases: [...group].reverse(), // oldest → newest
      status: deriveMachineChunkStatus(newest),
      live: isLiveLease(newest) ? newest : null,
      lane: boardLane(newest),
      projectName: project?.name ?? newest.projectSlug,
      projectColor: projectColorFor(project),
    });
  }
  return rows;
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
