import type { BacklogPeekEntry, QueuePeekEntry } from '../../api/hub';

/** One ready-queue entry, unblocked and unscoped, at whatever chunk and position the caller names. */
export function queuePeekEntry(
  overrides: Pick<QueuePeekEntry, 'chunk_id' | 'position'> & Partial<QueuePeekEntry>,
): QueuePeekEntry {
  return {
    blocked: null,
    graph_id: 'gr_build',
    project_id: null,
    project_name: null,
    project_slug: null,
    work_refs: [],
    ...overrides,
  };
}

/** One backlog entry, unblocked and unscoped, at whatever chunk and position the caller names. */
export function backlogPeekEntry(
  overrides: Pick<BacklogPeekEntry, 'chunk_id' | 'position'> & Partial<BacklogPeekEntry>,
): BacklogPeekEntry {
  return {
    blocked: null,
    graph_id: 'gr_build',
    project_id: null,
    project_name: null,
    project_slug: null,
    work_refs: [],
    ...overrides,
  };
}
