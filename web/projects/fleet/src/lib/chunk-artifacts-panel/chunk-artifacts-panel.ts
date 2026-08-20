import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { ArtifactView } from '../api/hub';
import { ChunkArtifactBody, sortArtifacts } from '../chunk-detail';
import { KitAsyncState, type KitAsyncStateValue } from '../kit';
import { FleetWhen } from '../when-display';

/**
 * A chunk's artifact store as a nav list beside a viewer — daemon-agnostic,
 * off the shared {@link ArtifactView} shape alone rather than a whole `ChunkDetail`, so any
 * host with an artifact list and a URL-held selection can mount it verbatim. Lifted out of
 * the hub board's own Artifacts tab (which shipped this exact list-plus-viewer shape first)
 * so the runner's chunk detail page gets the same look and feel without duplicating it —
 * `ChunkArtifacts`'s own row-per-artifact list stays put for the desktop dock instead,
 * which has no room for a two-pane layout.
 *
 * Presentational, and the viewer is a pure function of its inputs — no internal selection
 * state. `selectedKey` is the raw URL param the consumer's own selection owns; an absent
 * key resolves to the **most recent** entry, and a key naming nothing in the store resolves
 * to the empty state rather than silently falling back to something else.
 *
 * The viewer composes {@link ChunkArtifactBody} in `full` mode — the single owner of an
 * artifact's rendering.
 */
@Component({
  selector: 'fleet-chunk-artifacts-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactBody, FleetWhen, KitAsyncState],
  templateUrl: './chunk-artifacts-panel.html',
  styleUrl: './chunk-artifacts-panel.css',
})
export class ChunkArtifactsPanel {
  /** The chunk's artifact store, server-ordered. */
  readonly artifacts = input.required<readonly ArtifactView[]>();

  /** The raw selection param — the requested entry, before this component falls it
   * back to the most recent one. */
  readonly selectedKey = input<string | null>(null);

  /** Roots every `data-testid` this component renders, the same convention
   * {@link ChunkArtifactBody}'s own `testid` input follows. Defaults to
   * `'artifacts-panel'` — the runner's chunk detail page and this component's
   * own spec both read that name. The hub's chunk detail page overrides it to
   * `'artifacts-tab'`, the name its Artifacts tab carried before this panel
   * absorbed it, because `DemoDirector`'s unattended kiosk tour steers by that
   * exact string outside any test harness — a rename here would silently stop
   * the demo mid-tour rather than fail a build. */
  readonly testidPrefix = input('artifacts-panel');

  /** Emitted with a nav row's key when the operator picks it. */
  readonly pickArtifact = output<string>();

  protected readonly sortedArtifacts = computed(() => sortArtifacts(this.artifacts()));

  /** Gates the nav list through {@link KitAsyncState} rather than a hand-rolled empty
   * line — not a query state (there is no read in flight here, just an empty store), but
   * `placement="inline"` is the app's own established convention for a list panel's empty
   * copy (`questions-view.html`, `runner-view.html`, `event-log-view.html` all read it the
   * same way), so an empty artifact store reads as a considered state, not an unstyled
   * fragment. */
  protected readonly navState = computed<KitAsyncStateValue>(() => (this.sortedArtifacts().length === 0 ? 'empty' : 'ready'));

  /** {@link selectedKey}, defaulted to the most recent entry when absent. Stays
   * whatever `selectedKey` names when it names nothing in the store — that is
   * the dead-link case {@link viewState} resolves to `empty`, not a case to
   * paper over with a silent fallback. */
  protected readonly effectiveKey = computed<string | null>(() => {
    const key = this.selectedKey();
    if (key !== null) return key;
    const sorted = this.sortedArtifacts();
    return sorted.length > 0 ? sorted[sorted.length - 1].key : null;
  });

  protected readonly selectedArtifact = computed<ArtifactView | undefined>(() => {
    const key = this.effectiveKey();
    if (key === null) return undefined;
    return this.artifacts().find((art) => art.key === key);
  });

  protected readonly viewState = computed<KitAsyncStateValue>(() => {
    if (this.artifacts().length === 0) return 'empty';
    return this.selectedArtifact() === undefined ? 'empty' : 'ready';
  });

  protected readonly emptyMessage = computed(() => (this.artifacts().length === 0 ? 'No artifacts yet.' : 'NO SUCH ARTIFACT'));
}
