import { type Signal, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';

/**
 * A chunk selection held in the current route's `?chunk=` query param. The URL
 * is the single source of truth: a consumer derives its selection from this
 * param and every pick writes it back, never the reverse, so the surface's
 * link is copyable, a reload keeps its place, and back/forward walk the
 * selection history.
 */
export interface ChunkUrlSelection {
  /** The selected `chunk_id` as the URL names it, or `null`. Unvalidated — a
   * consumer checks it against its own live data before treating it as a real
   * selection, so an id naming a chunk that no longer exists degrades to
   * no-selection. */
  readonly chunkId: Signal<string | null>;

  /** Merge a selection into the URL — a client-side navigation (no reload)
   * that pushes a history entry. `null` clears `chunk`. Touches no other
   * param: `chunk` is the only one this helper owns. */
  select(chunkId: string | null): void;
}

/** Bind {@link ChunkUrlSelection} to the current route. Call from an injection
 * context (a component field initializer or constructor). */
export function injectChunkUrlSelection(): ChunkUrlSelection {
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
        queryParams: { chunk: chunkId },
        queryParamsHandling: 'merge',
      });
    },
  };
}
