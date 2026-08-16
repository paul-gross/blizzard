import { type Signal, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';

/** The runner chunk detail page's tabs (issue #318 follow-up) — mirrors the
 * hub's own {@link ChunkDetailTab} (`hub`'s `chunk-detail-selection.ts`), on
 * this page's own three regions. */
export type RunnerChunkDetailTab = 'general' | 'artifacts' | 'transcripts';

/**
 * The chunk detail page's tab selection, as the URL holds it — `tab` names
 * the active tab. The same contract the hub's own `chunk-detail-selection.ts`
 * establishes: the URL is the single source of truth, so a link is copyable,
 * a reload keeps its place, and back/forward walk the selection.
 *
 * This page already owns `?attempt=` (`chunk-detail-page.ts`'s own docstring,
 * D4) before this tab strip existed — {@link select} merges `tab` into the
 * URL rather than replacing it, so `?attempt=` survives a tab switch and a
 * deep link can carry both at once.
 */
export interface ChunkDetailSelection {
  /** The active tab. An absent or unrecognized `tab` param resolves to
   * `'general'` — never a fourth, ungoverned state. */
  readonly tab: Signal<RunnerChunkDetailTab>;

  /** Merge a tab into the URL — a client-side navigation (no reload) that
   * pushes a history entry, leaving every other query param (`?attempt=`
   * included) untouched. */
  select(tab: RunnerChunkDetailTab): void;
}

const TABS: readonly RunnerChunkDetailTab[] = ['general', 'artifacts', 'transcripts'];

/** Bind {@link ChunkDetailSelection} to the current route. Call from an
 * injection context (a component field initializer or constructor). */
export function injectChunkDetailSelection(): ChunkDetailSelection {
  const route = inject(ActivatedRoute);
  const router = inject(Router);
  // Seeded from the current snapshot so the first render already reflects a
  // deep-linked URL rather than one frame of the default tab.
  const params = toSignal(route.queryParamMap, { initialValue: route.snapshot.queryParamMap });

  return {
    tab: computed(() => {
      const raw = params().get('tab');
      return (TABS as readonly string[]).includes(raw ?? '') ? (raw as RunnerChunkDetailTab) : 'general';
    }),
    select(tab: RunnerChunkDetailTab): void {
      void router.navigate([], {
        relativeTo: route,
        queryParams: { tab },
        queryParamsHandling: 'merge',
      });
    },
  };
}
