import type { WorkItemEntry } from './api/hub';
import type { KitAsyncStateValue } from './kit/kit-async-state';

/** The structural shape `asyncState`/`asyncStateOf` need from a query — exactly
 * what `injectQuery()`'s return value exposes, named so this file needs no
 * `@tanstack` import (`bzh:frontend-kit-floor` keeps the kit itself query-free;
 * this helper lives beside `query-keys.ts` for the same reason). */
export interface AsyncStateQuery {
  isPending(): boolean;
  isError(): boolean;
}

/**
 * Derives a container's `KitAsyncStateValue` from one query: any pending state
 * wins as `'loading'`, else an error as `'error'`, else `isEmpty` decides
 * `'empty'` vs `'ready'`.
 *
 * Reads `isPending()`, never `isFetching()` — TanStack reports `pending` only
 * while there is no data at all; a background refetch (a poll, an SSE-driven
 * invalidation) sets `isFetching` and leaves `status: 'success'`, so it never
 * regresses an already-rendered view back to `'loading'`.
 *
 * Trap: a **disabled** query (`enabled: false`) also reports `isPending()` as
 * `true` forever — there is no read in flight to resolve it. A container for a
 * conditional query (e.g. chunk-detail with nothing selected) must branch on
 * its own "nothing selected" rest state *before* calling this helper, or that
 * rest state renders as a permanent loading spinner instead.
 */
export function asyncState(query: AsyncStateQuery, isEmpty: boolean): KitAsyncStateValue {
  if (query.isPending()) return 'loading';
  if (query.isError()) return 'error';
  return isEmpty ? 'empty' : 'ready';
}

/**
 * The multi-query form of {@link asyncState}: any of the given queries pending
 * wins as `'loading'`, else any of them in error as `'error'`, else `isEmpty`
 * decides `'empty'` vs `'ready'` — for a view whose emptiness depends on more
 * than one read (e.g. the board, whose lane order and chunk data are separate
 * queries).
 */
export function asyncStateOf(queries: readonly AsyncStateQuery[], isEmpty: boolean): KitAsyncStateValue {
  if (queries.some((query) => query.isPending())) return 'loading';
  if (queries.some((query) => query.isError())) return 'error';
  return isEmpty ? 'empty' : 'ready';
}

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
 * isPending/isError/success fold {@link asyncState} derives for the generic
 * triad, specialized to carry the resolved `items` alongside it rather than a
 * bare {@link KitAsyncStateValue} — every container mounting the issue pane
 * (the desktop dock's `chunk-detail.ts`, the hub's `chunk-page.ts`, the
 * runner's `chunk-detail-page.ts`) shares this one derivation rather than
 * duplicating it. `isPending()` is checked before `isError()`, which is
 * harmless rather than a real precedence choice: TanStack's underlying
 * `status` is mutually exclusive, so a query is never both at once.
 */
export function deriveWorkItemsState(query: WorkItemsQuery): WorkItemsState {
  if (query.isPending()) return { status: 'loading', items: [] };
  if (query.isError()) return { status: 'error', items: [] };
  return { status: 'success', items: query.data()?.items ?? [] };
}
