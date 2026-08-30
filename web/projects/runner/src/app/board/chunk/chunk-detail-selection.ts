import { type Signal, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';

/** The runner chunk detail page's tabs (now widened for Node history) —
 * mirrors the hub's own {@link ChunkDetailTab} (`hub`'s `chunk-detail-selection.ts`), on
 * this page's own four regions. */
export type RunnerChunkDetailTab = 'general' | 'node-history' | 'artifacts' | 'transcripts';

/**
 * The chunk detail page's tab selection, as the URL holds it — `tab` names
 * the active tab, `artifact` the artifact selected within the Artifacts tab,
 * `step` the node-step selected within the Node history tab. The same
 * contract the hub's own `chunk-detail-selection.ts` establishes: the URL is
 * the single source of truth, so a link is copyable, a reload keeps its
 * place, and back/forward walk the selection.
 *
 * This page already owns `?attempt=` (`chunk-detail-page.ts`'s own docstring,
 * D4) before this tab strip existed — every navigation here merges into the
 * URL rather than replacing it, so `?attempt=` and every other selection
 * survive a tab switch and a deep link can carry all of them at once.
 */
export interface ChunkDetailSelection {
  /** The active tab. An absent or unrecognized `tab` param resolves to
   * `'general'` — never a fifth, ungoverned state. */
  readonly tab: Signal<RunnerChunkDetailTab>;

  /** The raw `artifact` param — the artifact key selected in the Artifacts
   * tab, or `null`. Unvalidated: a key naming nothing in the store is the
   * Artifacts panel's own dead-link state to resolve. */
  readonly artifactKey: Signal<string | null>;

  /** The raw `step` param — the node-step key selected in the Node history
   * tab, or `null`. Unvalidated, the same stance as {@link artifactKey}. */
  readonly stepKey: Signal<string | null>;

  /** The raw `segment` param — the transcript segment id opened in the
   * Transcripts tab, or `null`. Unvalidated, the same stance as {@link artifactKey}. */
  readonly transcriptSegment: Signal<string | null>;

  /** The raw `sidechain` param — an encoded `SidechainPath` (`fleet`'s
   * `transcript-sidechain-path.ts`) naming the sidechain, nested under a tool
   * call or unlinked, opened standalone within the open segment, or `null`. */
  readonly transcriptSidechain: Signal<string | null>;

  /** Merge a tab into the URL — a client-side navigation (no reload) that
   * pushes a history entry, leaving every other query param (`?attempt=`,
   * `?artifact=`, `?step=`) untouched. */
  select(tab: RunnerChunkDetailTab): void;

  /** Pick an artifact in the Artifacts tab — switches to that tab and writes
   * its key back to the URL. */
  selectArtifact(key: string): void;

  /** Select a node-step (or close one with `null`) in the Node history tab —
   * switches to that tab and writes the step's join key back to the URL. */
  selectStep(stepKey: string | null): void;

  /** Open a transcript segment (or close one with `null`) — clears any
   * standalone-opened sidechain, since it belongs to the previously open
   * segment. Mirrors the hub's own `chunk-detail-selection.ts`. */
  selectTranscriptSegment(segmentId: string | null): void;

  /** Open (or close, with `null`) a sidechain standalone within the
   * currently open segment, addressed by its encoded `SidechainPath`. */
  selectTranscriptSidechain(path: string | null): void;
}

const TABS: readonly RunnerChunkDetailTab[] = ['general', 'node-history', 'artifacts', 'transcripts'];

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
    artifactKey: computed(() => params().get('artifact')),
    stepKey: computed(() => params().get('step')),
    transcriptSegment: computed(() => params().get('segment')),
    transcriptSidechain: computed(() => params().get('sidechain')),
    select(tab: RunnerChunkDetailTab): void {
      void router.navigate([], {
        relativeTo: route,
        queryParams: { tab },
        queryParamsHandling: 'merge',
      });
    },
    selectArtifact(key: string): void {
      void router.navigate([], {
        relativeTo: route,
        queryParams: { tab: 'artifacts', artifact: key },
        queryParamsHandling: 'merge',
      });
    },
    selectStep(stepKey: string | null): void {
      void router.navigate([], {
        relativeTo: route,
        queryParams: { tab: 'node-history', step: stepKey },
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
