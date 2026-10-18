import type { WorkItemEntry } from '../../api/hub';

/** The chunk's related work items and the state of the pass-through fetch, for the work-item column.
 *
 * `loading` while the forge read is in flight, `error` when the whole read failed (an
 * unreachable hub or no work-source configured — the pane shows a visible notice, AC5), and
 * `success` with `items` (possibly empty for a chunk with no pointers — the empty state, AC4;
 * a per-item `error` carries a single pointer's forge failure the pane notices in place). */
export interface WorkItemsState {
  readonly status: 'loading' | 'error' | 'success';
  readonly items: readonly WorkItemEntry[];
}

/** The shape {@link deriveWorkItemsState} needs from a work-items query — both
 * the runner's and the hub's generated query clients satisfy it structurally. */
export interface WorkItemsQuery {
  isPending(): boolean;
  isError(): boolean;
  data(): { items?: readonly WorkItemEntry[] } | undefined;
}

/**
 * Derives a {@link WorkItemsState} from a work-items query — the same
 * isPending/isError/success fold `query-state.ts`'s {@link asyncState} derives
 * for the generic triad, specialized to carry the resolved `items` alongside it.
 */
export function deriveWorkItemsState(query: WorkItemsQuery): WorkItemsState {
  if (query.isPending()) return { status: 'loading', items: [] };
  if (query.isError()) return { status: 'error', items: [] };
  return { status: 'success', items: query.data()?.items ?? [] };
}
