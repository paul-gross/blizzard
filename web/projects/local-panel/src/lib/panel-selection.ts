import { type Signal, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';

/**
 * The panel's selection, as the URL holds it (issue #99) — `chunk` names the
 * selected chunk and `attempt` the selected attempt lease within it. The URL is
 * the single source of truth: the panel derives its state from these params and
 * every selection writes them back, never the reverse, so a link is copyable, a
 * reload keeps its place, and back/forward walk the selection history.
 *
 * Owned here rather than in `LocalPanel` so the container is left with what it
 * is actually for — the local-API reads and the one derived-status fold — and
 * the router coupling lives in one small, separately testable place.
 */
export interface PanelSelection {
  /** The selected `chunk_id`, or `null`. An id naming a chunk not on this
   * machine degrades to no-selection: nothing in the list matches it. */
  readonly chunkId: Signal<string | null>;

  /** The raw `attempt` param — the *requested* attempt lease, before the
   * container falls it back to the chunk's newest. */
  readonly attemptLeaseId: Signal<string | null>;

  /** Merge a selection into the URL — a client-side navigation (no reload)
   * that pushes a history entry. `null` clears a param. */
  select(chunkId: string | null, attemptLeaseId: string | null): void;
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
    attemptLeaseId: computed(() => params().get('attempt')),
    select(chunkId: string | null, attemptLeaseId: string | null): void {
      void router.navigate([], {
        relativeTo: route,
        queryParams: { chunk: chunkId, attempt: attemptLeaseId },
        queryParamsHandling: 'merge',
      });
    },
  };
}
