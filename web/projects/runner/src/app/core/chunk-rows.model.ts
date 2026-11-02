import type { runnerApi, Tone } from 'fleet';

import { type MachineChunkStatus, deriveMachineChunkStatus } from './chunk-status';
import { type PanelLease, type PanelProject, projectColorFor } from './panel-reads.model';
import { isLiveLease } from './worker-session';

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

/** The board's lanes with their headings, and the {@link Tone} each lane's count reads in. */
export const BOARD_LANES: readonly { readonly key: BoardLane; readonly label: string; readonly tone: Tone }[] = [
  { key: 'needs', label: 'Needs human', tone: 'needs' },
  { key: 'progress', label: 'In progress', tone: 'running' },
  { key: 'done', label: 'Done', tone: 'done' },
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
