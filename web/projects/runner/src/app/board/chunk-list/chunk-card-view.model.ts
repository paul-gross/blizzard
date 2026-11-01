import { type ChunkCardView, compactRef, type runnerApi } from 'fleet';

import type { MachineChunkRow } from '../../core/chunk-rows.model';
import { showsChunkStatus } from '../app-panel.model';

/**
 * A chunk's folded row as the fleet card and list row draw it: its machine-side status ({@link MachineChunkRow}'s
 * lease-derived fold) as the word and tone, the runner holding it and the step its newest session is on, and its
 * linked work items, each named by its label — or its ref, where it has none — over its title. The status is named
 * only where the live worker's own line does not already say it ({@link showsChunkStatus}).
 */
export function machineChunkCardView(row: MachineChunkRow, linkedItems: readonly runnerApi.WorkItemEntry[]): ChunkCardView {
  const { lease, status } = row;
  const shown = showsChunkStatus(row) ? status.label : null;
  return {
    chunkId: lease.chunk_id,
    shortId: compactRef(lease.chunk_id),
    status: status.label,
    tone: status.tone,
    statusWord: shown,
    rowStatus: shown,
    step: lease.node_name,
    stepTitle: lease.node_name,
    workLines: linkedItems.map((item) => ({ label: item.label ?? item.ref, title: item.title, url: item.web_url })),
    itemsRead: true,
    holder: { host: null, name: lease.owner.label, title: lease.owner.label },
    projectName: row.projectName,
    projectColor: row.projectColor,
  };
}
