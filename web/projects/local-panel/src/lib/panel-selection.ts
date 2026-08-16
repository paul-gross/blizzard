import { type Signal, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';

/**
 * The board's chunk selection, as the URL holds it (issue #99) — `chunk` names
 * the selected chunk. The URL is the single source of truth: the panel derives
 * its state from this param and every selection writes it back, never the
 * reverse, so a link is copyable, a reload keeps its place, and back/forward
 * walk the selection history.
 *
 * Owned here rather than in `LocalPanel` so the container is left with what it
 * is actually for — the local-API reads and the one derived-status fold — and
 * the router coupling lives in one small, separately testable place.
 *
 * Carries no `attempt` selection (issue #318 moved per-attempt selection to
 * the chunk detail route's own `?attempt=` read, `chunk-detail-page.ts`,
 * which owns that param independently rather than through this helper) —
 * an earlier revision did, but every real caller always wrote `null` for it.
 */
export interface PanelSelection {
  /** The selected `chunk_id`, or `null`. An id naming a chunk not on this
   * machine degrades to no-selection: nothing in the list matches it. */
  readonly chunkId: Signal<string | null>;

  /** Merge a selection into the URL — a client-side navigation (no reload)
   * that pushes a history entry. `null` clears `chunk`. Also clears `attempt`
   * (the chunk detail route's own param, `chunk-detail-page.ts`) whenever
   * it's present, so picking a different chunk on the board can never leave
   * a stale attempt selection parked in the URL for whichever chunk is
   * opened next — the board itself never reads `attempt` either way. */
  select(chunkId: string | null): void;
}

/** Bind {@link PanelSelection} to the current route. Call from an injection
 * context (a component field initializer or constructor). */
export function injectPanelSelection(): PanelSelection {
  const route = inject(ActivatedRoute);
  const router = inject(Router);
  // Seeded from the current snapshot so the first render already reflects a
  // deep-linked URL rather than one frame of no-selection.
  const params = toSignal(route.queryParamMap, { initialValue: route.snapshot.queryParamMap });

  return {
    chunkId: computed(() => params().get('chunk')),
    select(chunkId: string | null): void {
      void router.navigate([], {
        relativeTo: route,
        queryParams: { chunk: chunkId, attempt: null },
        queryParamsHandling: 'merge',
      });
    },
  };
}
