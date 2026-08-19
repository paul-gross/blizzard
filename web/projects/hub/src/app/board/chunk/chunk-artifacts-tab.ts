import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import {
  type ArtifactView,
  ChunkArtifactBody,
  FleetWhen,
  KitAsyncState,
  type KitAsyncStateValue,
  sortArtifacts,
} from 'fleet';

/**
 * The chunk detail page's Artifacts tab (issue #160) — a nav list beside a
 * viewer, replacing the desktop dock's inline stack the way the mobile
 * shell's per-artifact page already did, but without leaving this page for a
 * deeper route: the viewer swaps in place as the operator picks a different
 * entry.
 *
 * Presentational, and the viewer is a pure function of its inputs — no
 * internal selection state. `selectedKey` is the raw `?artifact` URL param;
 * {@link ChunkPage} owns writing it back when a nav row is picked (the
 * `board-selection`/`panel-selection` pattern), so a picked row, a reload,
 * and a shared link all resolve the same way. An absent key resolves to the
 * **most recent** entry; a key naming nothing in the store — a stale dock
 * link, or a garbage `?artifact` — resolves to the empty state
 * ({@link ArtifactPage} takes the same stance on a stale deep link) rather
 * than silently falling back to something else.
 *
 * The viewer composes {@link ChunkArtifactBody} in `full` mode — the single
 * owner of an artifact's rendering, the same component the dock's link rows
 * (`summary` mode) and the mobile single-artifact page compose.
 */
@Component({
  selector: 'app-chunk-artifacts-tab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkArtifactBody, FleetWhen, KitAsyncState],
  templateUrl: './chunk-artifacts-tab.html',
  styleUrl: './chunk-artifacts-tab.css',
})
export class ChunkArtifactsTab {
  /** The chunk's artifact store, server-ordered. */
  readonly artifacts = input.required<readonly ArtifactView[]>();

  /** The raw `?artifact` URL param — the requested selection, before this
   * component falls it back to the most recent entry. */
  readonly selectedKey = input<string | null>(null);

  /** Emitted with a nav row's key when the operator picks it. */
  readonly pickArtifact = output<string>();

  protected readonly sortedArtifacts = computed(() => sortArtifacts(this.artifacts()));

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
