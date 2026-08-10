import { type Signal, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';

/** The chunk detail page's tabs (issue #160, widened blizzard#248 Phase 2). */
export type ChunkDetailTab = 'general' | 'artifacts' | 'transcripts';

/**
 * The chunk detail page's selection, as the URL holds it (issue #160, widened
 * blizzard#248 D7/D9) — `tab` names the active tab; `artifact` the artifact
 * selected within the Artifacts tab; `segment`/`sidechain` the Transcripts
 * tab's own open segment and, within it, a standalone-opened sidechain
 * conversation — nested under a tool call or unlinked, either carries one
 * (`review:F4`). The URL is the single source of truth: the page derives its
 * state from these params and every selection writes them back, never the
 * reverse, so a link is copyable, a reload keeps its place, back/forward walk
 * the selection, and the board dock's artifact link is a plain `routerLink`
 * with no page-side wiring needed.
 *
 * The same contract `board-selection.ts` and `local-panel/panel-selection.ts`
 * establish, on this page's own params. Owned here rather than in
 * {@link ChunkPage} so the container is left with what it is actually for —
 * the chunk reads and the operator mutations — and the router coupling lives
 * in one small, separately readable place.
 */
export interface ChunkDetailSelection {
  /** The active tab. An absent or unrecognized `tab` param resolves to
   * `'general'` — never a fourth, ungoverned state. */
  readonly tab: Signal<ChunkDetailTab>;

  /** The raw `artifact` param — the artifact key selected in the Artifacts
   * tab, or `null`. Unvalidated: a key naming nothing in the store is the
   * Artifacts tab's own dead-link state to resolve. */
  readonly artifactKey: Signal<string | null>;

  /** The raw `segment` param — the transcript segment id opened in the
   * Transcripts tab, or `null`. Unvalidated, the same stance as `artifactKey`. */
  readonly transcriptSegment: Signal<string | null>;

  /** The raw `sidechain` param — an encoded `SidechainPath` (`fleet`'s
   * `transcript-sidechain-path.ts`) naming the sidechain, nested under a tool
   * call or unlinked, opened standalone within the open segment
   * (blizzard#248 D7, `review:F4`), or `null`. */
  readonly transcriptSidechain: Signal<string | null>;

  /** Merge a selection into the URL — a client-side navigation (no reload)
   * that pushes a history entry. `artifactKey` of `null` clears the param. */
  select(tab: ChunkDetailTab, artifactKey: string | null): void;

  /** Open a transcript segment (or close one with `null`) — clears any
   * standalone-opened sidechain, since it belongs to the previously open
   * segment. */
  selectTranscriptSegment(segmentId: string | null): void;

  /** Open (or close, with `null`) a sidechain standalone within the
   * currently open segment, addressed by its encoded `SidechainPath`
   * (`review:F4`). */
  selectTranscriptSidechain(path: string | null): void;
}

const TABS: readonly ChunkDetailTab[] = ['general', 'artifacts', 'transcripts'];

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
      return (TABS as readonly string[]).includes(raw ?? '') ? (raw as ChunkDetailTab) : 'general';
    }),
    artifactKey: computed(() => params().get('artifact')),
    transcriptSegment: computed(() => params().get('segment')),
    transcriptSidechain: computed(() => params().get('sidechain')),
    select(tab: ChunkDetailTab, artifactKey: string | null): void {
      void router.navigate([], {
        relativeTo: route,
        queryParams: { tab, artifact: artifactKey },
        queryParamsHandling: 'merge',
      });
    },
    selectTranscriptSegment(segmentId: string | null): void {
      void router.navigate([], {
        relativeTo: route,
        queryParams: { tab: 'transcripts', segment: segmentId, sidechain: null },
        queryParamsHandling: 'merge',
      });
    },
    selectTranscriptSidechain(path: string | null): void {
      void router.navigate([], {
        relativeTo: route,
        queryParams: { sidechain: path },
        queryParamsHandling: 'merge',
      });
    },
  };
}
