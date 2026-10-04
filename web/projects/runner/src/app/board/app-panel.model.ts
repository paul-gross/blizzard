import type { runnerApi } from 'fleet';

import { type MachineChunkStatus, deriveMachineChunkStatus } from './chunk-list/chunk-status';

/** One row in the machine-chunks list: a chunk's newest lease plus its derived
 * machine-side status, pre-folded so the layout needs no second read. `leases`
 * carries *every* attempt of the chunk (oldest → newest) — the detail dock
 * resolves its own summary off the newest entry without a second read of its
 * own; `lease` is that same newest entry, already resolved for the row. */
export interface MachineChunkRow {
  readonly lease: runnerApi.LeaseView;
  readonly leases: readonly runnerApi.LeaseView[];
  readonly status: MachineChunkStatus;
}

/** The *active* leases only — a closed lease is history, carried by
 * {@link machineChunkRows} as its chunk's newest attempt instead. */
export function activeLeases(leases: readonly runnerApi.LeaseView[]): runnerApi.LeaseView[] {
  return leases.filter((lease) => lease.state !== 'closed');
}

/** The desktop chunks pane's empty-state text — distinguishes "nothing on this
 * machine" from "the filter hid everything", naming the hidden count so the
 * operator knows to check "show all". */
export function chunksEmptyText(total: number, visible: number): string {
  if (total === 0) return 'NO CHUNKS ON THIS MACHINE';
  const hidden = total - visible;
  return `${hidden} CHUNK${hidden === 1 ? '' : 'S'} HIDDEN BY THE FILTER — CHECK SHOW ALL`;
}

/**
 * One row per chunk on this machine: the chunk's newest lease (the server
 * orders actives first, then the recent-closed block, so the first lease
 * seen per `chunk_id` is the freshest attempt) plus every attempt of the
 * chunk and the derived status. Each row's `leases` is ordered oldest → newest,
 * so `lease` (the summary/status subject) is that list's own newest entry.
 */
export function machineChunkRows(
  leases: readonly runnerApi.LeaseView[],
  dashboard: runnerApi.DashboardView | undefined,
): MachineChunkRow[] {
  const facts = {
    escalatedChunkIds: new Set((dashboard?.escalations?.items ?? []).map((esc) => esc.chunk_id)),
    takeoverChunkIds: new Set((dashboard?.takeovers?.items ?? []).map((tko) => tko.chunk_id)),
    askChunkIds: new Set((dashboard?.asks?.items ?? []).map((ask) => ask.chunk_id)),
  };
  // Group by chunk in server order (newest attempt first); the Map preserves
  // first-seen insertion order, so the rows keep the newest-lease-first order.
  const grouped = new Map<string, runnerApi.LeaseView[]>();
  for (const lease of leases) {
    const group = grouped.get(lease.chunk_id);
    if (group) group.push(lease);
    else grouped.set(lease.chunk_id, [lease]);
  }
  const rows: MachineChunkRow[] = [];
  for (const group of grouped.values()) {
    const newest = group[0];
    rows.push({
      lease: newest,
      leases: [...group].reverse(), // oldest → newest
      status: deriveMachineChunkStatus(newest, facts),
    });
  }
  return rows;
}

/** The rows the "show all" filter leaves visible — every row when `showAll`, else
 * rows whose *derived* {@link MachineChunkStatus.tone} isn't `done`/`idle` (not raw
 * lease state), so a closed lease with an open escalation still shows as `NEEDS HUMAN`. */
export function visibleChunkRows(rows: MachineChunkRow[], showAll: boolean): MachineChunkRow[] {
  if (showAll) return rows;
  return rows.filter((chunk) => chunk.status.tone !== 'done' && chunk.status.tone !== 'idle');
}

function rowFor(rows: readonly MachineChunkRow[], chunkId: string | null): MachineChunkRow | undefined {
  if (chunkId === null) return undefined;
  return rows.find((chunk) => chunk.lease.chunk_id === chunkId);
}

/** The chunk's attempts (oldest → newest), or empty when nothing is selected or the chunk is unknown. */
export function chunkLeasesFor(
  rows: readonly MachineChunkRow[],
  chunkId: string | null,
): readonly runnerApi.LeaseView[] {
  return rowFor(rows, chunkId)?.leases ?? [];
}

/** The chunk's derived status, or `null` when nothing is selected or the chunk is unknown. */
export function chunkStatusFor(rows: readonly MachineChunkRow[], chunkId: string | null): MachineChunkStatus | null {
  return rowFor(rows, chunkId)?.status ?? null;
}

/** The chunk's open escalation, when one exists — carries the resume command. */
export function escalationFor(
  dashboard: runnerApi.DashboardView | undefined,
  chunkId: string | null,
): runnerApi.EscalationView | null {
  if (chunkId === null) return null;
  return (dashboard?.escalations?.items ?? []).find((esc) => esc.chunk_id === chunkId) ?? null;
}
