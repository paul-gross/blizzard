import { type Signal, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';

/**
 * The board's chunk selection, as the URL holds it (issue #162) — `chunk` names
 * the card the operator opened in the detail dock. The URL is the single source
 * of truth: the board derives its selection from this param and every pick
 * writes it back, never the reverse, so a board link is copyable, a reload keeps
 * its place, and back/forward walk the selection history.
 *
 * The same contract issue #99 established for the runner's local panel
 * (`local-panel/panel-selection.ts`), on the one thing this surface can select —
 * the board has no attempt tabs, so `chunk` is the whole selection. Owned here
 * rather than in {@link BoardPage} so the container is left with what it is
 * actually for — the fleet reads and the promote mutation — and the router
 * coupling lives in one small, separately readable place.
 *
 * A query param rather than a path segment: `/board` is one URL serving two
 * shells (`app.routes.ts`), and selection is a *state* of that page, not a
 * different page. Encoding it in the path would fork the deep link the mobile
 * guard matches on.
 */
export interface BoardSelection {
  /** The selected `chunk_id` as the URL names it, or `null`. Unvalidated — the
   * board checks it against the live fleet list before opening the dock, so an
   * id naming a chunk that no longer exists degrades to no-selection. */
  readonly chunkId: Signal<string | null>;

  /** Merge a selection into the URL — a client-side navigation (no reload) that
   * pushes a history entry. `null` clears the param. */
  select(chunkId: string | null): void;
}

/** Bind {@link BoardSelection} to the current route. Call from an injection
 * context (a component field initializer or constructor). */
export function injectBoardSelection(): BoardSelection {
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
